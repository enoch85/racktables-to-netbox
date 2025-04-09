#!/usr/bin/env python3
"""
Wrapper script to run the migration from Racktables to NetBox

This script imports the custom_netbox module to extend the NetBox library with
the necessary methods for migration, then runs the migration script with these
extensions applied.
"""

import os
import sys
import importlib.util
import argparse
from custom_netbox import NetBoxWrapper

# Keep the original environment
original_env = dict(os.environ)

def parse_arguments():
    """Parse command line arguments for the migration script"""
    parser = argparse.ArgumentParser(description='Migrate data from Racktables to NetBox')
    parser.add_argument('--site', type=str, help='Target site name to restrict migration to')
    parser.add_argument('--config', type=str, help='Path to configuration file')
    return parser.parse_args()

def run_migration(args):
    """Run the migration script with the updated NetBox wrapper class"""
    print("Starting Racktables to NetBox migration with enhanced NetBox library...")
    
    # Import the migrate_extended module
    spec = importlib.util.spec_from_file_location("migrate_additional", "extended_migrate.py")
    migrate_additional = importlib.util.module_from_spec(spec)
    
    # Create a global variable 'NetBox' in the builtins
    # This simulates having 'from netbox import NetBox' available everywhere
    import builtins
    builtins.NetBox = NetBoxWrapper
    
    try:
        # Execute the migrate_additional module
        spec.loader.exec_module(migrate_additional)
        
        # Set the target site if specified
        if args.site:
            print(f"Target site specified: {args.site}")
            # Set the TARGET_SITE variable in the migrate module
            migrate_additional.TARGET_SITE = args.site
        
        print("Migration completed successfully!")
        return True
        
    except Exception as e:
        print(f"Error during migration: {str(e)}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        # Clean up the global we added
        if hasattr(builtins, 'NetBox'):
            delattr(builtins, 'NetBox')
        
        # Restore original environment
        os.environ.clear()
        os.environ.update(original_env)

if __name__ == "__main__":
    # Parse command line arguments
    args = parse_arguments()
    
    # First run set_custom_fields.py to ensure all custom fields are created
    print("Setting up custom fields...")
    custom_fields_spec = importlib.util.spec_from_file_location("set_custom_fields", "set_custom_fields.py")
    custom_fields = importlib.util.module_from_spec(custom_fields_spec)
    custom_fields_spec.loader.exec_module(custom_fields)
    
    # Run the migration
    success = run_migration(args)
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)
