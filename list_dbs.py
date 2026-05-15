import os
import json
import urllib.request
from google.oauth2 import service_account
from google.auth.transport.requests import Request

with open('firebase_env.txt', 'r') as f:
    creds_dict = json.load(f)

creds = service_account.Credentials.from_service_account_info(
    creds_dict, scopes=['https://www.googleapis.com/auth/cloud-platform']
)
creds.refresh(Request())

project_id = creds_dict['project_id']
url = f"https://firestore.googleapis.com/v1/projects/{project_id}/databases"

req = urllib.request.Request(url, headers={'Authorization': f'Bearer {creds.token}'})
try:
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
        print("Available databases:")
        for db in data.get('databases', []):
            print(f"- {db['name']} (ID: {db['name'].split('/')[-1]})")
except Exception as e:
    print(f"Failed to list databases: {e}")
