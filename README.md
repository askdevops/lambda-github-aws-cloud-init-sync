## AWS Lambda function that pulls SSH keys from GitHub, syncs them with AWS key pairs and generates cloud init template


#### Features:
* Reads GitHub repo SSH key files and syncs (adds/deletes) them with AWS key pairs
* Dynamically generates cloud-init template compatible with EC2 UserData
* Uploads cloud-init template to S3
* Allows local testing

#### Prerequisites:
* Python version => 3
