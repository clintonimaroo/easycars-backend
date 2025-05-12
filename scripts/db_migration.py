#!/usr/bin/env python3
import sys
import time
import logging
import argparse
from pymongo import MongoClient
import os
import sys

# Add the project root directory to Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Import the settings
from app.config.env import settings

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def parse_args():
    """Parse command-line arguments"""
    parser = argparse.ArgumentParser(description='Migrate data between MongoDB instances')
    
    parser.add_argument('--source-uri', help=f'Source MongoDB URI (default: {settings.MONGODB_SOURCE_URI})')
    parser.add_argument('--source-db', help=f'Source database name (default: {settings.MONGODB_SOURCE_DB})')
    
    parser.add_argument('--target-uri', help=f'Target MongoDB URI (default: {settings.MONGODB_URI})')
    parser.add_argument('--target-db', help=f'Target database name (default: {settings.DATABASE_NAME})')
    
    parser.add_argument('--collections', nargs='+', default=['users', 'vehicles', 'notifications'],
                        help='Collections to migrate (default: users, vehicles, notifications)')
    
    parser.add_argument('--skip-existing', action='store_true', 
                        help='Skip collections that already have data')
    
    return parser.parse_args()

def get_connection_params(args):
    """Get connection parameters from args or settings"""
    # Source connection
    source_uri = args.source_uri or settings.MONGODB_SOURCE_URI
    source_db = args.source_db or settings.MONGODB_SOURCE_DB
    
    # Target connection
    target_uri = args.target_uri or settings.MONGODB_URI
    target_db = args.target_db or settings.DATABASE_NAME
    
    # Validate required parameters
    if not source_uri:
        raise ValueError("Source MongoDB URI not provided (use --source-uri or set MONGODB_SOURCE_URI)")
    
    if not source_db:
        raise ValueError("Source database name not provided (use --source-db or set MONGODB_SOURCE_DB)")
    
    if not target_uri:
        raise ValueError("Target MongoDB URI not provided (use --target-uri or set MONGODB_URI)")
    
    if not target_db:
        raise ValueError("Target database name not provided (use --target-db or set DATABASE_NAME)")
    
    return source_uri, source_db, target_uri, target_db

def migrate_collection(source_collection, target_collection, skip_existing=False):
    """Migrate data from source collection to target collection"""
    # Check if target has data already
    target_count = target_collection.count_documents({})
    
    if target_count > 0 and skip_existing:
        logger.info(f"Skipping {target_collection.name} (already has {target_count} documents)")
        return 0
    
    # Get documents from source
    source_docs = list(source_collection.find({}))
    source_count = len(source_docs)
    
    if source_count == 0:
        logger.info(f"Source collection {source_collection.name} is empty, skipping")
        return 0
    
    logger.info(f"Migrating {source_count} documents from {source_collection.name}")
    
    # Insert into target
    if source_count > 0:
        result = target_collection.insert_many(source_docs)
        inserted = len(result.inserted_ids)
        logger.info(f"Inserted {inserted} documents into {target_collection.name}")
        return inserted
    
    return 0

def main():
    """Main migration function"""
    # Parse arguments
    args = parse_args()
    
    try:
        # Get connection parameters
        source_uri, source_db, target_uri, target_db = get_connection_params(args)
        
        logger.info(f"Connecting to source database: {source_uri}")
        source_client = MongoClient(source_uri)
        source_db = source_client[source_db]
        
        logger.info(f"Connecting to target database: {target_uri}")
        target_client = MongoClient(target_uri)
        target_db = target_client[target_db]
        
        # Migrate collections
        total_migrated = 0
        for collection_name in args.collections:
            source_collection = source_db[collection_name]
            target_collection = target_db[collection_name]
            
            migrated = migrate_collection(
                source_collection, 
                target_collection,
                skip_existing=args.skip_existing
            )
            
            total_migrated += migrated
        
        logger.info(f"Migration completed. Total documents migrated: {total_migrated}")
        
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        return 1
    finally:
        # Close connections
        if 'source_client' in locals():
            source_client.close()
        
        if 'target_client' in locals():
            target_client.close()
    
    return 0

if __name__ == "__main__":
    sys.exit(main()) 