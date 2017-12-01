# CiscoSparkADlicense
Script to associate licenses/entitlement in Cisco Spark based on Active Directory group membership

Cisco Spark Directory Sync allows to automatically provision users in Spark based on existing users in Microsoft Active Directory. 
The attached script provides that functionality. It assumes that users are organised into multiple distinct organisational units (OU). 
In each OU a AD group is created to map specific licenses to member users. For each OU the script has to be run separately.

To configure the script go to Spark for Developers List Licenses and retrieve the access token to authorise API calls and the license key for the Spark Org.

Enter the access code into the header definition of the script
headers = {
 'Authorization' : 'Bearer <access token from developer.ciscospark.com goes here>',
 'Content-Type' : 'application/json'
 }

Enter the Spark license IDs and the Active Directory group names into the script and establish the mapping between them. 
Example shows only two groups and licenses, this can be extended to meet individual needs (in addition to messaging and meeting entitlements hybrid services such as calendar, call aware and call connect can be associated in the same way). 

Cisco Spark licenses that should be associated with a specific Active Directory group
can be retrieved from https://api.ciscospark.com/v1/licenses
sparkmessage_lic = 'Y2lzY29zcGFyazovL3VzL0xJQ0VOU0UvZDczYjIGMtZ7QjVjNy00MGRkLTlhNzMtMDkyM2IyZDBiZWQ0Ok1TXzJlYmRjNmU1LWFkOTktNGE4OS1hY2IxLWRiYjgxOTNkOGEzYw'
webexmessage_lic = 'Y2lzY29zcGFyazovL3VzL0xJQ0VOU0UvZDczYjI0VGMtZjVjNy00MGRkLTlhNzMtMDkyM2IyZDBiZWQ0OkVFXzk5YzU5NzAwLTlmNjgtNGU1OC04YzYzLTk2OTQ4YmZhMmUxM19pZGVudGl0eWxhYjEyYS53ZWJleC5jb20'

Active Directort groups
sparkmsggroup = "SparkMessagingTest"
#sparkmsggroup = "SparkMessaging"
webexgroup = "WebexMeetingsTest"

List of dict that contains mapping between groups and licenses
todo = []
todo.append({"adgroup": sparkmsggroup, "spklicense": sparkmessage_lic})
todo.append({"adgroup": webexgroup, "spklicense": webexmessage_lic})
The script does some sanity checking, i.e. it checks the number of available licenses in the cloud and if the number required to sync the AD group members is larger than the available it requires the parameter --overwrite to be set. Similarly the default for maximum number of changes permitted is set to 500. In case more updates are required in a single run adjust the parameter --maxupdates=<number of updates required>. 
Another option provided is --trialrun where the script runs and provides information about what update are required but does NOT make any modifications to the cloud. In case interested (or something goes sideways) --log=<log level> allows to see more what is going on. 
 
Did some limited testing and verification, feel free to use and provide feedback.

