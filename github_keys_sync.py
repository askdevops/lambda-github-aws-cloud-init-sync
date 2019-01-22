import os
import requests
import boto3
import botocore
from jinja2 import Template
from json import loads, dumps


def get_repo_contents(dir_path, headers):
    resp = requests.get(dir_path, headers=headers)

    if (100 <= resp.status_code < 600) and (resp.status_code != 200):
        print(resp.url)
        raise ConnectionError('Error: Bad response', resp.status_code, resp.reason)

    contents = loads(resp.content.decode('utf-8'))

    return contents


def fetch_users(contents, dir_path, headers):
    user_keys = {}

    for each in contents:
        file_path = dir_path + each['name']
        username = each['name'].split('.')[0]

        key = requests.get(file_path, headers=headers).content.decode('utf-8')

        # remove blank lines for consistency
        ssh_key = "".join([s for s in key.strip().splitlines(True) if s.strip("\r\n").strip()])

        print('Fetched:', username)
        print(ssh_key)

        user_keys[username] = ssh_key

    print('\nTotal:  ', len(user_keys), 'users')

    return user_keys


# generate cloud init config file from jinja template
def render_template(user_keys):
    if not user_keys:
        raise Exception('ERROR: No users fetched from GitHub')

    with open('init.j2') as f:
        template = Template(f.read())

    print('\nRendering config file from template')
    config = template.render(users=user_keys.items())
    clean_config = "".join([s for s in config.strip().splitlines(True) if s.strip("\r\n").strip()])

    with open('/tmp/init.conf', 'w') as f:
        f.write(clean_config)
    print('Done')


# upload config file to S3
def upload_to_s3(bucket_name):
    file_path = '/tmp/init.conf'
    filename = file_path.split('/')[-1]

    config = os.path.isfile(file_path)

    if not config:
        return False

    print('\nUploading', filename, 'to', bucket_name)
    s3 = boto3.client('s3')

    try:
        s3.upload_file(file_path, bucket_name, filename)
    except Exception as error:
        print('Error: Upload Failed\n', error)
        return False

    print('Upload Finished\n')

    return True


# Import github ssh keys to AWS Key Pairs
def upload_aws_key_pairs(users):
    ec2 = boto3.client('ec2')

    imported_keys = []
    existing_keys = []

    print('Searching for new AWS key pairs to import')
    for user, key in users.items():
        key_name = user + '-gh-key'

        if key.find('ed25519') != -1:
            print('WARNING: Cant import', key_name, '(ed25519 keys are not supported by AWS)\n', key)

        try:
            ec2.import_key_pair(
                KeyName=key_name,
                PublicKeyMaterial=key
            )
        except botocore.exceptions.ClientError as e:
            if e.response['Error']['Code'] == 'InvalidKeyPair.Duplicate':
                print(user, 'AWS key pair already exists (',key_name, ') Skipping...\n')
                existing_keys.append(key_name)
        except Exception as error:
            print('ERROR: Something went wrong during AWS key import\n', error)
            return False
        else:
            print('Importing new key:', key_name)
            imported_keys.append(key_name)

    if existing_keys:
        print(len(existing_keys),'Existing:', existing_keys)
    if imported_keys:
        print(len(imported_keys),'Imported:', imported_keys)
    if not existing_keys and not imported_keys:
        print('No Existing Keys Found and Nothing Imported')

    all_aws_keys = existing_keys + imported_keys

    all_aws_keys.sort()

    return all_aws_keys


# compare github keys and AWS key pairs and remove those that don't exist in github
def clean_aws_key_pairs(github_keys):
    if not github_keys:
        raise Exception('ERROR: No AWS key pairs found that match GitHub')

    ec2 = boto3.client('ec2')

    aws_key_pairs = []
    aws_deleted_keys = []

    resp = ec2.describe_key_pairs()
    for kn in resp['KeyPairs']:
        key_name = kn['KeyName']
        if key_name.find('-gh-key') != -1: # we only care about keys that came from github (-gh-key in their name)
            aws_key_pairs.append(key_name)

    aws_key_pairs.sort()

    for key in aws_key_pairs:
        # remove AWS key pairs with -gh-key in their name that are not in github
        if key not in github_keys:
            print('WARNING:', key, 'not found in GitHub')
            print('Deleting:', key)
            resp = ec2.delete_key_pair(KeyName=key)
            print(resp)
            aws_deleted_keys.append(key)

    if aws_deleted_keys:
        print('Deleted Keys:', aws_deleted_keys)

    return aws_deleted_keys


def github_auth_handler(req_id):
    expected_id = str(os.environ.get('repo_id'))

    if expected_id == req_id:
        return True
    else:
        return False


def http_response(status):
    if status == 200:
        print('HTTP Status: 200')
        response = {
            'statusCode': 200,
            'body': dumps({'Status': 'SUCCESS'})
                }
    elif status == 403:
        print('HTTP Status: 403')
        response = {
            'statusCode': 403,
            'body': dumps({'Access': 'DENIED'})
            }
    else:
        print('HTTP Status: 500')
        response = {
            'statusCode': 500,
            'body': dumps({'Status': 'FAILED'})
            }

    return response


def lambda_handler(event, context):
    if context is not 'local':
        req_payload = loads(event['body'])
        req_repo_id = str(req_payload['repository']['id'])
        print(req_payload)

        authorized = github_auth_handler(req_repo_id)

        if not authorized:
            return http_response(403)
        else:
            print('Authentication Check Passed')

    dir = os.environ.get('contents_url')
    dir = dir.rstrip('/') + '/'

    github_token = os.environ.get('github_token')
    s3_bucket = os.environ.get('s3_bucket')

    hdrs = {'Authorization': 'token ' + github_token,
            'Accept': "application/vnd.github.v3.raw"}

    data = get_repo_contents(dir_path=dir, headers=hdrs)

    users = fetch_users(data, dir_path=dir, headers=hdrs)

    render_template(users)

    aws_key_pairs = upload_aws_key_pairs(users)

    if not aws_key_pairs:
        return http_response(500)

    clean_aws_key_pairs(aws_key_pairs)

    uploaded = upload_to_s3(s3_bucket)

    if uploaded:
        return http_response(200)
    else:
        return http_response(500)


if __name__ == "__main__":
    # For local testing
    os.environ['contents_url'] = "" # github path from where to fetch ssh key files
    os.environ['github_token'] = "" # initialize with a valid github token with read permissions
    os.environ['s3_bucket'] = "" # name of S3 bucket where to save cloud-init template

    event = 'Local Test'

    lambda_handler(event, 'local')
