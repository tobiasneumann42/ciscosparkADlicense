'''
MIT License

Copyright (c) 2017 Cisco Systems, Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

@autor Tobias Neumann, tneumann@cisco.com
created 26.11.2017
version 1.0

'''
import requests
import json
import asyncio
import aiohttp
import logging as log
from aiohttp import ClientSession
import sys
from ldap3 import Server, Connection, AUTO_BIND_NO_TLS, SUBTREE, BASE, ALL_ATTRIBUTES, ObjectDef, AttrDef, Reader, Entry, Attribute, OperationalAttribute
import re
import backoff
import time
# do some pretty stuff later
#import progressbar

# Global definitions
# Cisco Spark authentication headers
headers = {
    'Authorization' : 'Bearer <access token from developer.ciscospark.com goes here>',
    'Content-Type' : 'application/json'
    }
# Base URL for Cisco Spark API requests
base = 'https://api.ciscospark.com/v1'

# Connection parameters to Active Directory
AD_SERVER = 'sparkhdsad01.sparkhds.com'
AD_BASE = 'OU=SparkUsersTest,DC=sparkhds, DC=com'
#AD_BASE = 'OU=SparkUsers,DC=sparkhds, DC=com'
AD_ADMIN = 'administrator@sparkhds.com'
AD_PASSWORD = '<password>'

# Number of concurrent tasks executed against Cisco Spark API
MAX_CONCURRENT_REQUESTS = 10
sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
# Maximum number of updates permitted
maxupdates = 500
# Trial mode control
trialrun = False

# Cisco Spark licenses that should be associated with a specific Active Directory group
# can be retrieved from https://api.ciscospark.com/v1/licenses
sparkmessage_lic = 'Y2lzY29zcGFyazovL3VzL0xJQ0VOU0UvZDczYjI0ZGMtZjVjNy00MGRkLTlhNzMtMDkyM2IyZDBiZWQ0Ok1TXzJlYmRjNmU1LWFkOTktNGE4OS1hY2IxLWRiYjgxOTNkOGEzYw'
webexmessage_lic = 'Y2lzY29zcGFyazovL3VzL0xJQ0VOU0UvZDczYjI0ZGMtZjVjNy00MGRkLTlhNzMtMDkyM2IyZDBiZWQ0OkVFXzk5YzU5NzAwLTlmNjgtNGU1OC04YzYzLTk2OTQ4YmZhMmUxM19pZGVudGl0eWxhYjEyYS53ZWJleC5jb20'

# Active Directort groups
sparkmsggroup = "SparkMessagingTest"
#sparkmsggroup = "SparkMessaging"
webexgroup = "WebexMeetingsTest"

# List of dict that contains mapping between groups and licenses
todo = []
todo.append({"adgroup": sparkmsggroup, "spklicense": sparkmessage_lic})
todo.append({"adgroup": webexgroup, "spklicense": webexmessage_lic})

# Variable to control amount of logging
my_loglevel = False
# Counter for number of 429 retries encountered during REST calls
retr429 = 0

# Logging stuff
LEVELS = {'debug': log.DEBUG,
          'info': log.INFO,
          'warning': log.WARNING,
          'error': log.ERROR,
          'critical': log.CRITICAL}

def get_AD_members( ad_filter ):
	conn = Connection(Server(AD_SERVER, port=3268, use_ssl=False),
                auto_bind=AUTO_BIND_NO_TLS, user=AD_ADMIN,
                password=AD_PASSWORD)
	# (memberOf:1.2.840.113556.1.4.1941:=cn=HdsTestGroup,OU=SparkUsers,DC=sparkhds,DC=com)
	conn.search(AD_BASE, ad_filter, search_scope=SUBTREE, attributes=['mail'], size_limit=0)

	if len(conn.response) > 0:
		members = []
		for entry in conn.entries:
			members += entry['mail'].values
		return members
	else:
		mlog.debug("Active Directory query to object {} with filter {} did return zero results".format(AD_BASE, ad_filter))
		return

def get_group_members( ad_group ):
	ldap_filter = '(&(objectClass=user)(sAMAccountName=*)(memberOf:1.2.840.113556.1.4.1941:=cn=' + ad_group + ',' + AD_BASE + '))'
	# (memberOf:1.2.840.113556.1.4.1941:=cn=HdsTestGroup,OU=SparkUsers,DC=sparkhds,DC=com)
	members = get_AD_members( ldap_filter )
	#print(conn.entries)

	return members

def backoff_exception_handler(details):
	global retr429
	retr429 += 1
	#print ( "rate limited backing off for {details['wait']:0.1f} seconds after {details['tries']}")

@backoff.on_exception(backoff.constant, (aiohttp.client_exceptions.ClientResponseError,
										aiohttp.ServerConnectionError,
										aiohttp.client_exceptions.ServerTimeoutError,
										asyncio.TimeoutError,
										asyncio.CancelledError),
										jitter=backoff.random_jitter,
										interval=5,
					  					on_backoff=backoff_exception_handler,
					  					max_tries=20)
async def send_rest_mk1( sess, method, url, **kwargs):
	async with sem:
		async with getattr(sess, method)( url, **kwargs) as response:
			data = await response.json()
			response.close()
			return data

async def get_user_info( user_mail, sess):
	params={ 'email' : user_mail }
	response = await send_rest_mk1( sess, 'get', base + '/people', params = params, headers = headers)
	return response['items'][0]

async def update_user(uobj, sess):
	response = await send_rest_mk1(sess, 'put', base + '/people/'+uobj['id'], headers=headers, data=json.dumps(uobj))
	return response

def obj_find(lst, key, value):
	for i, dic in enumerate(lst):
		if type(dic[key]) is list and value in dic[key]:
			return i
		elif dic[key] == value:
			return i
	return -1

# fucntion to add or remove licenses from user objects
# verifies if user object is already in list of ojbects to update u2update
# in that case additional modifications are being made to that object and
# no additional entry is created in u2update
# in case on object exists append additional objects
def mod_updates( mod_op, wrk_obj, u2update, ou_obj ):
	dub_index = obj_find(u2update, 'emails', ou_obj['emails'][0])
	if dub_index != -1:
		if mod_op == 'add':
			u2update[dub_index]['licenses'].append(wrk_obj['license'])
		else:
			u2update[dub_index]['licenses'].remove(wrk_obj['license'])
	else:
		if mod_op == 'add':
			ou_obj['licenses'].append(wrk_obj['license'])
		else:
			ou_obj['licenses'].remove(wrk_obj['license'])
		u2update.append(ou_obj)
	return u2update

async def add_lic_run( loop, oumembers, grpmembers):
	tasks = []

	async with ClientSession(loop=loop, raise_for_status=True) as session:
		ou_reponses = []
		# gather spark user object for each member of Active Directory OU
		# gather some metrics on how long this takes
		start_time = time.time()
		for ou_user in oumembers:
			# no need to wait for each call to the Cisco Spark API
			# starting async tasks for each request
			task = asyncio.ensure_future(get_user_info( ou_user, session))
			tasks.append(task)
		mlog.info(" Total number of tasks {} ".format(len(asyncio.Task.all_tasks())))
		# wait for all tasks to finish and store all async response
		ou_responses = await asyncio.gather(*tasks)
		# only print additional info if user asked for it
		mlog.info("Rest execution took {} ".format(time.time()-start_time))
		mlog.info("Number of 429 Retries from REST requests {} ".format(retr429))
		mlog.info("Number of REST responses {} ".format(len(ou_responses)))

		# create list object to store all users requiring update (add or remove license)
		users2update = []
		# iterate through OU members to identify which objects we need to remove
		# or add license
		for ou_user in ou_responses:
			# check for users that have a license but are not member of respective AD group
			# will have the license removed
			for each_user in grpmembers:
				# user does have icense but is not member of AD group -> remove license
				if (each_user['license'] in ou_user['licenses'] and ou_user['emails'][0] not in each_user['members']):
					mlog.info("USER LICENSE TO BE REMOVED {} ".format(ou_user['emails'][0]))
					# already have an update object for that user, modify existing object
					# do not add another one, would require more unnecessary REST API calls
					users2update = mod_updates( 'remove', each_user, users2update, ou_user )
				# user does not have license and is member of AD group -> add_license
				elif each_user['license'] not in ou_user['licenses'] and ou_user['emails'][0] in each_user['members']:
						# append license to user object
						mlog.debug("USER LICENSE TO BE ADDED {} ".format(ou_user['emails'][0]))
						users2update = mod_updates( 'add', each_user, users2update, ou_user )
		# check if there are objects to update and iterate through the list
		# this applies to both adding and removing
		mlog.info("TOTAL NUMBERS OF OBJECTS REQUIRING UPATE (ADD/REMOVE): {} ".format(len(users2update)))
		# check if the number of proposed updates is greater than the configured maximum
		# default 500
		if len(users2update) > maxupdates:
			print("Number of changes/updates proposed is greater than maximum number of allowed updates.\n",
				  "To proceed used the --maxupdates command line parameters. ")
			sys.exit()
		tasks = []
		ret429 = 0
		if trialrun:
			mlog.info("Trial run enabled, no updates will be executed!")
			print("Trial run enabled, no updates will be executed.")
		elif len(users2update) > 0 and not trialrun:
			update_responses = []
			# gather some metrics on how long this takes
			start_time = time.time()
			for up_users in users2update:
				mlog.debug( " TASK {} ".format(up_users['emails'][0]))
				# check if trial run is enabled, no updates to cloud in trial run mode
				task = asyncio.ensure_future(update_user(up_users, session))
				tasks.append(task)
			#mlog.info(" Total number of tasks {} ".format(len(asyncio.Task._current_tasks())))
			update_responses = await asyncio.gather(*tasks)
			mlog.info("Execution took {} ".format(time.time()-start_time))
			mlog.info("Number of 429 Retries from REST requests {} ".format(retr429))
			mlog.info("Number of responses from PUT update operatiosn {} ".format(len(update_responses)))

def get_license_info( lic_obj):
	r = requests.get( base + '/licenses/' + lic_obj, headers=headers)
	r.raise_for_status()
	lic_available = int(r.json()['totalUnits'])-int(r.json()['consumedUnits'])
	mlog.info(" Cisco Spark license {} units available {} ".format( r.json()['name'], lic_available))
	return lic_available

def not_used_get_license_assign( lic_members, spklic):
	r = requests.get('https://api.ciscospark.com/v1/people', headers=headers)
	r.raise_for_status()

	js = r.json()['items']
	#print(" ALL USERS ##################### ", js)
	assigned_users =[x for x in js if spklic in x['licenses']]

	#if http response header Link we have to deal with more pages
	while 'Link' in r.headers:
		page_url = re.findall(r'<(.*?)>', r.headers['Link'].split(';')[0])[0]
		page_attr = r.headers['Link'].split(';')[1]
		r = requests.get(page_url, headers=headers)
		r.raise_for_status()
		js = r.json()['items']
		assigned_users += [x for x in js if spklic in x['licenses']]
	print(" Users with license assigned ", assigned_users)
	print(" Number of users ", len(assigned_users))
	#while r.headers['Link'] contains 'next':
	#	r = requests.get()
	return

def get_parse():
	from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter
	parser = ArgumentParser(description=" Add Cisco Spark user license based on Active Directory group memebership ",
				formatter_class=ArgumentDefaultsHelpFormatter)
	parser.add_argument("-ow", "--overwrite",
                        action="store_true",
                        dest="overwrite",
                        default=False,
                        help="Execute license assignment even when number of users to assign is larger than available licenses. Use with caution!")
	parser.add_argument("-l=", "--log",
                        action="store",
                        dest="my_loglevel",
                        default="ERROR",
                        help="Set level of detail what the script is doing")
	parser.add_argument("-tr", "--trialrun",
                        action="store_true",
                        dest="trialrun",
                        default=False,
                        help="Just check what happens no updates made")
	parser.add_argument("-mu", "--maxupdates",
						action="store",
						dest="maxupdates",
						default=500,
						help="Maximum number of update operations permitted by default")

	return parser.parse_args()

if __name__ == "__main__":
	args = get_parse()
	print(" Command line Args ", args)

	maxupdates = int(args.maxupdates)
	trialrun = args.trialrun

	log.basicConfig(level=LEVELS.get(str.lower(args.my_loglevel)),
					format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
	# set the backoff function to CRITIAL for logging, otherwise it will throw console
	# message for each 429 and there can be a lot of them
	log.getLogger('backoff').setLevel(log.CRITICAL)
	mlog = log.getLogger("set_lic_mk2")

	loop = asyncio.get_event_loop()

	# List that will store licenses and corresponding user members
	ad_groups = []
	# Gather all users in given Active Directory OU, required to verify
	# removal of licenses
	mlog.info("Fetching Active Directory members of OU {} ".format(AD_BASE))
	ou_members = get_AD_members('(&(objectClass=user)(sAMAccountName=*))')
	mlog.info("Number of users (mail addresses) of OU members {} ".format(len(ou_members)))

	# iterate through todo dict that contains Active Directory groups and
	# corresponding licenses
	for ad_obj in todo:
		#group_members = []
		mlog.info("Fetching members from Active Directory group {} ".format(ad_obj['adgroup']))
		group_members = get_AD_members('(&(objectClass=user)(sAMAccountName=*)(memberOf:1.2.840.113556.1.4.1941:=cn=' + ad_obj['adgroup'] + ',' + AD_BASE + '))')
		# in case all memebers of group have been removed, set list to empty otherwise existing license check will not work

		if not group_members:
			group_members = []
			mlog.info( "No members in group, could mean all licenses to be removed " )
		else:
			mlog.info("Number of group members {}".format(len(group_members)))
		# gather Cisco Spark available licenses for subscription
		lic_available = get_license_info(ad_obj['spklicense'])

		# only execute if group_members is NOT empty
		if (len(group_members) > lic_available and not args.overwrite):
			print("Number of users in Active Directory group could exceed number of available licenses.\n",
				"Proceeding with license assignement can cause over subscription. \n",
				"DEPENDING ON YOUR LICENSE AGREEMENT YOU MAY BE CHARGED! \n",
				"To proceed specify the overwrite parameter. ")
			sys.exit()
		# only execute if number of users in AD group is smaller than available licenses
		# or when overwrite parameter has been set
		# remove len 0 check - len 0 valid use case for remove license from all users in OU (no members in group)
		elif (len(group_members) > lic_available and args.overwrite) or \
			(len(group_members) <= lic_available):
			ad_grp_object = {}
			ad_grp_object['license'] = ad_obj['spklicense']
			ad_grp_object['members'] = group_members
			ad_groups.append(ad_grp_object)

	future = asyncio.ensure_future(add_lic_run(loop, ou_members, ad_groups))

	loop.run_until_complete(future)
