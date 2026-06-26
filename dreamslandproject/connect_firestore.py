import os
import firebase_admin
from firebase_admin import credentials, firestore

# Get the base directory of your Django project (where manage.py is located)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Correct path to serviceAccountKey.json
cred_path = os.path.join(BASE_DIR, "serviceAccountKey.json")

# Initialize Firebase app only once
if not firebase_admin._apps:
    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred)

# Firestore client
db = firestore.client()