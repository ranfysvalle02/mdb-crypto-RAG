# mdb-crypto-RAG

# Queryable Encryption + Automatic Encryption(Atlas) + RAG

### What You Need
Before you can use Queryable Encryption, you must set up the following items in your development environment:

- Download the Automatic Encryption Shared Library (recommended) or mongocryptd properly configured/setup
- Install a MongoDB Driver Compatible with Queryable Encryption
- Atlas Cluster running MongoDB 6.2
- Install specific driver dependencies.
- Azure KMS + Azure KeyVault + Azure Key (which will be our "master key")

### For this tutorial/demo, need to have mongocryptd or the shared_lib properly configured. This is a critical step
https://www.mongodb.com/docs/manual/core/queryable-encryption/reference/shared-library
https://www.mongodb.com/docs/manual/core/queryable-encryption/reference/mongocryptd/

### You can set up Queryable Encryption using the following mechanisms:
- Automatic Encryption: Enables you to perform encrypted read and write operations without you having to write code to specify how to encrypt fields.
- Explicit Encryption: Enables you to perform encrypted read and write operations through your MongoDB driver's encryption library. You must specify the logic for encryption with this library throughout your application.

Today we will be focusing on Automatic Encryption using MongoDB Atlas. But before, lets talk a little bit about Envelope Encryption - which is what Queryable Encryption uses under the hood.

# Envelope Encryption
An analogy that helped me understand this was - think of a "Lockbox".
![alt text](https://ae01.alicdn.com/kf/H9e2485b079db4fe2abe2d8b5a7884a7bO/Key-Lock-Box-Combination-Lockbox-with-Code-for-House-Key-Storage-Combo-Door-Locker.jpg_.webp "Title")

We will have a Customer Master Key (CMK) - which in our analogy will be the combination to the lockbox itself.
And with this CMK, we will protect the "keys inside".

The "keys inside" are going to be called the Data Encryption Keys (DEK). These are the keys that will be used to actually encrypt/decrypt the fields. And access to them will be protected by the CMK.

## Encrypt
![alt text](https://rockelitix-ituwr.mongodbstitch.com/imageedit_4_9354567901.png "Title")

## Decrypt
![alt text](https://rockelitix-ituwr.mongodbstitch.com/imageedit_5_5000319473.png "Title")



# Automatic Encryption/Decryption
Automatic encryption essentially fetches the keys from the keyvault automatically and lets you get straight to encrypting/decrypting.
To encrypt/decrypt your fields automatically, you must configure your MongoClient instance as follows:
- Specify your Key Vault collection
- Specify a kmsProviders object

# Writing an Encrypted Field
![alt text](https://www.mongodb.com/docs/manual/images/CSFLE_Write_Encrypted_Data.png "Title")

# Reading an Encrypted Field
![alt text](https://www.mongodb.com/docs/manual/images/CSFLE_Read_Encrypted_Data.png "Title")

# Now for the good stuff. Let's see some code in action!

# Password Manager with MongoDB and Azure OpenAI

This tutorial will guide you through the process of creating a password manager using MongoDB and Azure OpenAI. We will be using MongoDB's Client-Side Field Level Encryption (CSFLE) feature to encrypt sensitive data and Azure OpenAI for generating embeddings and chat completions.

## Prerequisites

- Python 3.6 or later
- MongoDB server
- Azure OpenAI account

## Setup

1. Clone the repository and navigate to the project directory.

2. Install the required Python packages:

```bash
pip install pymongo[encryption] openai
```

3. Set up your MongoDB server and make sure it's running.

4. Set up your Azure OpenAI account and get your API key.

## Usage

The script `password_manager.py` is the main entry point of the application. It performs the following tasks:

- Generates a local master key and sets up the KMS providers.
- Configures the MongoClient to use the local KMS provider.
- Creates a data encryption key.
- Encrypts a sample document and inserts it into the collection.
- Creates a MongoDB Atlas Vector Search index.
- Queries the encrypted data using the encrypted client.
- Uses Azure OpenAI to generate chat completions.

To run the script, use the following command:

```bash
python3 password_manager.py
```

## Code Explanation

The script starts by importing the necessary modules and setting up the Azure OpenAI client with the endpoint, API version, and API key.

The `generate_embeddings` function is used to generate embeddings for a given text using the Azure OpenAI client.

A local master key is generated and the KMS providers are set up with this key. The MongoClient is then configured to use the local KMS provider.

The script then creates a data encryption key and encrypts a sample document. The encrypted document is inserted into the collection.

A MongoDB Atlas Vector Search index is created for the encrypted collection. The script then waits for the index to be ready before proceeding.

The script then queries the encrypted data using the encrypted client and prints the result.

Finally, the script uses the Azure OpenAI client to generate chat completions using the queried data.

## Troubleshooting

If you encounter any errors while running the script, make sure that your MongoDB server is running and that you have correctly set up your Azure OpenAI account.
