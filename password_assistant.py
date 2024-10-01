import os
import time
import pymongo
from pymongo.encryption import AutoEncryptionOpts, ClientEncryption, Algorithm
from bson.codec_options import CodecOptions
from bson.binary import STANDARD
from openai import AzureOpenAI
from pymongo.operations import SearchIndexModel
from datetime import datetime, timedelta


VS_INPUT = "family" # this will be used for vector search
azure_endpoint="https://demo.openai.azure.com"
api_version="2024-04-01-preview"
api_key=""
az_client = AzureOpenAI(azure_endpoint=azure_endpoint, api_version=api_version, api_key=api_key)
def generate_embeddings(text, model="text-embedding-ada-002"): 
        return az_client.embeddings.create(input = [text], model=model).data[0].embedding
# Generate a 96-byte local master key
local_master_key = os.urandom(96)
# Set up the KMS providers with the local master key
kms_providers = {"local": {"key": local_master_key}}
key_vault_namespace = "encryption.__pymongoTestKeyVault"
# Configure the MongoClient to use the local KMS provider
csfle_opts = AutoEncryptionOpts(
    kms_providers=kms_providers,
    key_vault_namespace=key_vault_namespace
)
client = pymongo.MongoClient(auto_encryption_opts=csfle_opts)
try:
    key_vault_db, key_vault_coll = key_vault_namespace.split(".", 1)
    key_vault = client[key_vault_db][key_vault_coll]
    key_vault.drop()  # Clear existing data
    key_vault.create_index("keyAltNames", unique=True)
except pymongo.errors.InvalidName as e:
    print(f"Error creating key vault collection: {e}")
    # Handle invalid collection name errors gracefully
except pymongo.errors.DuplicateKeyError as e:
    print(f"Error creating index: {e}")
    # Handle duplicate index errors gracefully
try:
    client_encryption = ClientEncryption(
        kms_providers,
        key_vault_namespace,
        client,
        CodecOptions(uuid_representation=STANDARD)
    )
except pymongo.errors.EncryptionError as e:
    # Handle encryption errors gracefully
    print(f"Error creating ClientEncryption: {e}")
    # You might want to take specific actions here, such as retrying or logging
# Create a data encryption key
key_id1 = client_encryption.create_data_key("local", key_alt_names=["example1"])
key_id2 = client_encryption.create_data_key("local", key_alt_names=["example2"])
key_id3 = client_encryption.create_data_key("local", key_alt_names=["example3"])

# Encrypt a sample document and insert it into the collection
encrypted_database_name = "test_db"
encrypted_collection_name = "pwd_manager"
encrypted_fields_map = {
    "fields": [
        {
            "path": "passwordRecord.username",
            "bsonType": "string",
            "keyId": key_id1,
            "queries": {"queryType": "equality"}
        },
        {
            "path": "passwordRecord.password",
            "bsonType": "string",
            "keyId": key_id2,
        },
        {
            "path": "owner_id",
            "bsonType": "string",
            "keyId": key_id3,
            "queries": {"queryType": "equality"}
        },
    ]
}

# Create the encrypted collection
client_encryption.create_encrypted_collection(
    client[encrypted_database_name],
    encrypted_collection_name,
    encrypted_fields_map,
    "local",
    local_master_key
)

# Insert encrypted documents
password_manager_document_1 = {
     "owner_id": "demo.user",
    "passwordRecord": {
        "website": "www.facebook.com",
        "website_type": "Social Media",
        "username": "jondoe1",
        "password": "password1",
        "metadata": {
            "creation_date": "2020-01-01",
            "last_updated": "2020-01-01",
            "notes": "Account created in Winter 2020 to reconnect with old friends.",
            "notes_embeddings":generate_embeddings("Account created in Winter 2020 to reconnect with old friends.")
        }
    },
    "logs_info": [{
        "ipAddress": "192.0.2.0",
        "attemptStatus": "success",
        "website": "www.facebook.com",
        "timestamp": datetime.now()-timedelta(hours=3),
    }, {
        "ipAddress": "192.0.2.1",  # Suspicious IP
        "attemptStatus": "fail",
        "website": "www.facebook.com",
        "timestamp": datetime.now()-timedelta(hours=2),
    }, {
        "ipAddress": "192.0.2.1",
        "attemptStatus": "fail",
        "website": "www.facebook.com",
        "timestamp": datetime.now()-timedelta(hours=1),
    }]
}

password_manager_document_2 = {
     "owner_id": "demo.user",
    "passwordRecord": {
        "website": "www.amazon.com",
        "website_type": "E-commerce",
        "username": "jondoe2",
        "password": "password2",
        "metadata": {
            "creation_date": "2020-06-01",
            "last_updated": "2020-06-01",
            "notes": "Account created in Summer 2020 for online shopping.",
            "notes_embeddings":generate_embeddings("Account created in Summer 2020 for online shopping.")
        }
    },
    "logs_info": [{
        "ipAddress": "192.0.2.0",
        "attemptStatus": "success",
        "website": "www.amazon.com",
        "timestamp": datetime.now()-timedelta(hours=3),
    }, {
        "ipAddress": "192.0.2.0",
        "attemptStatus": "success",
        "website": "www.amazon.com",
        "timestamp": datetime.now()-timedelta(hours=2),
    }, {
        "ipAddress": "192.0.2.2", 
        "attemptStatus": "fail",
        "website": "www.amazon.com",
        "timestamp": datetime.now()-timedelta(hours=1),
    }]
}

password_manager_document_3 = {
     "owner_id": "demo.user",
    "passwordRecord": {
        "website": "www.nytimes.com",
        "website_type": "News",
        "username": "jondoe2",
        "password": "password3",
        "metadata": {
            "creation_date": "2020-11-01",
            "last_updated": "2020-11-01",
            "notes": "Account created in Fall 2020 for keeping up with global news.",
            "notes_embeddings":generate_embeddings("Account created in Fall 2020 for keeping up with global news.")
        }
    },
    "logs_info": [{
        "ipAddress": "192.0.2.0",
        "attemptStatus": "success",
        "website": "www.nytimes.com",
        "timestamp": datetime.now()-timedelta(hours=3),
    }, {
        "ipAddress": "192.0.2.0",
        "attemptStatus": "success",
        "website": "www.nytimes.com",
        "timestamp": datetime.now()-timedelta(hours=2),
    }, {
        "ipAddress": "192.0.2.3",  
        "attemptStatus": "fail",
        "website": "www.nytimes.com",
        "timestamp": datetime.now()-timedelta(hours=1),
    }]
}
encrypted_collection = client[encrypted_database_name][encrypted_collection_name]
result = encrypted_collection.insert_many([password_manager_document_1, password_manager_document_2, password_manager_document_3])

# Create MongoDB Atlas Vector Search index
# Define the search index model
search_index_model = SearchIndexModel(
    definition={
        "fields": [
            {
                "type": "vector",
                "numDimensions": 1536,
                "path": "passwordRecord.metadata.notes_embeddings",
                "similarity": "cosine"
            },
        ]
    },
    name="vector_index",
    type="vectorSearch",
)
encrypted_collection.create_search_index(search_index_model)
print("Lets wait for index to be ready...")
start_time = time.time()
# add sleep delay for at least 10 seconds, printing a time counter, to show progress
time.sleep(10)
print("Time elapsed: ", time.time() - start_time)
print(list(encrypted_collection.list_search_indexes()))
# Query the encrypted data using the encrypted client
query_result = list(encrypted_collection.aggregate([
        {
            "$vectorSearch": {
                "index": "vector_index",
                "queryVector": az_client.embeddings.create(model="text-embedding-ada-002",input=VS_INPUT ).data[0].embedding,
                "path": "passwordRecord.metadata.notes_embeddings",
                "limit": 1,
                "numCandidates": 30
            },
        },
        {"$match": {"owner_id": "demo.user"}}, #queryable encryption
        {"$project":{"logs_info":1, "passwordRecord.username":1, "passwordRecord.website":1, "passwordRecord.website_type":1, "passwordRecord.metadata.creation_date":1, "passwordRecord.metadata.last_updated":1, "passwordRecord.metadata.notes":1}}
        ]))
print(
    query_result[0]
)
az_response = az_client.chat.completions.create(
                model="gpt-35-turbo", # model = "deployment_name".
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "[context]\n```" + str(query_result[0]) +"\n```\n[end context]"},
                    {"role": "user", "content": "Using ONLY the [context], create a summary report using emojis."},
                ]
            )
print(
    az_response.choices[0].message.content
)
