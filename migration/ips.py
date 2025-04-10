"""
IP-related migration functions
"""
import ipaddress
import random

from racktables_netbox_migration.utils import (
    get_db_connection, get_cursor, pickleLoad, pickleDump, 
    format_prefix_description, is_available_prefix
)
from racktables_netbox_migration.db import getTags, change_interface_name
from racktables_netbox_migration.config import IPV4_TAG, IPV6_TAG

def create_ip_networks(netbox, IP, target_site=None):
    """
    Create IP networks (prefixes) from Racktables in NetBox
    
    Args:
        netbox: NetBox client instance
        IP: "4" for IPv4 or "6" for IPv6
        target_site: Optional site name for filtering
    """
    print(f"\nCreating IPv{IP} Networks")
    
    # Load mapping of network IDs to VLAN info
    network_id_group_name_id = pickleLoad('network_id_group_name_id', dict())
    
    # Get existing prefixes to avoid duplicates
    existing_prefixes = set(prefix['prefix'] for prefix in netbox.ipam.get_ip_prefixes())
    
    # Retrieve networks from Racktables
    with get_db_connection() as connection:
        with get_cursor(connection) as cursor:
            cursor.execute(f"SELECT id,ip,mask,name,comment FROM IPv{IP}Network")
            ipv46Networks = cursor.fetchall()
    
    for network in ipv46Networks:
        Id, ip, mask, prefix_name, comment = network["id"], network["ip"], network["mask"], network["name"], network["comment"]
        
        # Skip the single IP addresses
        if (IP == "4" and mask == 32) or (IP == "6" and mask == 128): 
            continue
        
        prefix = str(ipaddress.ip_address(ip)) + "/" + str(mask)
        
        if prefix in existing_prefixes:
            continue
        
        # Get VLAN info if associated
        if Id in network_id_group_name_id:
            vlan_name = network_id_group_name_id[Id][1]
            vlan_id = network_id_group_name_id[Id][2]
        else:
            vlan_name = None
            vlan_id = None
        
        # Get tags for this network
        tags = getTags(f"ipv{IP}net", Id)
        
        # Determine if this network is available
        status = "Available" if is_available_prefix(prefix_name, comment) else "active"
        
        # Format description to include tags and prefix name
        description = format_prefix_description(prefix_name, tags, comment)
        
        # Create the prefix in NetBox
        try:
            netbox.ipam.create_ip_prefix(
                vlan={"id": vlan_id} if vlan_name else None,
                prefix=prefix,
                status=status,
                description=description,
                custom_fields={'Prefix_Name': prefix_name},
                tags=[{'name': IPV4_TAG if IP == "4" else IPV6_TAG}] + tags
            )
            print(f"Created {prefix} - {prefix_name}")
        except Exception as e:
            print(f"Error creating {prefix}: {e}")

def create_ip_allocated(netbox, IP, target_site=None):
    """
    Create allocated IP addresses from Racktables in NetBox
    
    Args:
        netbox: NetBox client instance
        IP: "4" for IPv4 or "6" for IPv6
        target_site: Optional site name for filtering
    """
    print(f"Creating allocated IPv{IP} Addresses")
    
    # Get existing IPs to avoid duplicates
    existing_ips = set(ip['address'] for ip in netbox.ipam.get_ip_addresses())
    
    # Get IP names and comments
    with get_db_connection() as connection:
        with get_cursor(connection) as cursor:
            cursor.execute(f"SELECT ip,name,comment FROM IPv{IP}Address")
            ip_addresses = cursor.fetchall()
            ip_names_comments = dict([(row["ip"], (row["name"], row["comment"])) for row in ip_addresses])
    
    # Get IP allocations (associations with devices)
    with get_db_connection() as connection:
        with get_cursor(connection) as cursor:
            cursor.execute(f"""
                SELECT ALO.object_id, ALO.ip, ALO.name, ALO.type, OBJ.objtype_id, OBJ.name 
                FROM IPv{IP}Allocation ALO, Object OBJ 
                WHERE OBJ.id=ALO.object_id
            """)
            ip_allocations = cursor.fetchall()
    
    # Filter by site if site filtering is enabled
    if target_site:
        # Get devices and VMs at the target site
        site_devices = set(device['name'] for device in netbox.dcim.get_devices(site=target_site))
        site_vms = set()
        
        # Get VMs in clusters at the target site
        site_clusters = netbox.virtualization.get_clusters(site=target_site)
        for cluster in site_clusters:
            cluster_vms = netbox.virtualization.get_virtual_machines(cluster_id=cluster['id'])
            site_vms.update(vm['name'] for vm in cluster_vms)
        
        # Filter allocations
        filtered_allocations = []
        for allocation in ip_allocations:
            device_name = allocation["name"].strip() if allocation["name"] else ""
            if device_name in site_devices or device_name in site_vms:
                filtered_allocations.append(allocation)
        
        ip_allocations = filtered_allocations
        print(f"Filtered to {len(ip_allocations)} IP allocations for site '{target_site}'")
    
    # Process each IP allocation
    for allocation in ip_allocations:
        object_id = allocation["object_id"]
        ip = allocation["ip"]
        interface_name = allocation["name"]
        ip_type = allocation["type"]
        objtype_id = allocation["objtype_id"]
        device_name = allocation["OBJ.name"]
        
        # Get IP name and comment if available
        if ip in ip_names_comments:
            ip_name, comment = ip_names_comments[ip]
        else:
            ip_name, comment = "", ""
        
        # Skip if device name is missing
        if not device_name:
            continue
        
        device_name = device_name.strip()
        
        # Format the IP address with CIDR notation
        string_ip = str(ipaddress.ip_address(ip)) + ("" if IP == "6" else "/32")
        
        # Skip if already exists (unless shared IP)
        if string_ip in existing_ips and ip_type != "shared":
            continue
        
        existing_ips.add(string_ip)
        
        # Set VRRP role if shared IP
        use_vrrp_role = "vrrp" if ip_type == "shared" else None
        
        # Standardize interface name
        if interface_name:
            interface_name = change_interface_name(interface_name, objtype_id)
        else:
            interface_name = f"no_RT_name{random.randint(0, 99999)}"
        
        # Determine if device is VM or physical device
        if objtype_id == 1504:  # VM
            device_or_vm = "vm"
            interface_list = netbox.virtualization.get_interfaces(virtual_machine=device_name)
        else:
            device_or_vm = "device"
            interface_list = netbox.dcim.get_interfaces(device=device_name)
        
        # Try to find matching interface
        device_contained_same_interface = False
        for name, interface_id in [(interface['name'], interface['id']) for interface in interface_list]:
            if interface_name == name:
                # Add IP to existing interface
                netbox.ipam.create_ip_address(
                    address=string_ip,
                    role=use_vrrp_role,
                    assigned_object={'device' if device_or_vm == "device" else "virtual_machine": device_name},
                    interface_type="virtual",
                    assigned_object_type="dcim.interface" if device_or_vm == "device" else "virtualization.vminterface",
                    assigned_object_id=interface_id,
                    description=comment[:200] if comment else "",
                    custom_fields={'IP_Name': ip_name, 'Interface_Name': interface_name, 'IP_Type': ip_type},
                    tags=[{'name': IPV4_TAG if IP == "4" else IPV6_TAG}]
                )
                device_contained_same_interface = True
                break
        
        # If no matching interface found, create a new virtual interface
        if not device_contained_same_interface:
            if device_or_vm == "device":
                device_id = netbox.dcim.get_devices(name=device_name)[0]['id']
            else:
                device_id = netbox.virtualization.get_virtual_machines(name=device_name)[0]['id']
            
            try:
                # Create a new virtual interface
                if device_or_vm == "device":
                    added_interface = netbox.dcim.create_interface(
                        name=interface_name,
                        interface_type="virtual",
                        device_id=device_id,
                        custom_fields={"Device_Interface_Type": "Virtual"}
                    )
                else:
                    added_interface = netbox.virtualization.create_interface(
                        name=interface_name,
                        interface_type="virtual",
                        virtual_machine=device_name,
                        custom_fields={"VM_Interface_Type": "Virtual"}
                    )
                
                # Add IP to the new interface
                netbox.ipam.create_ip_address(
                    address=string_ip,
                    role=use_vrrp_role,
                    assigned_object_id=added_interface['id'],
                    assigned_object={"device" if device_or_vm == "device" else "virtual_machine": {'id': device_id}},
                    interface_type="virtual",
                    assigned_object_type="dcim.interface" if device_or_vm == "device" else "virtualization.vminterface",
                    description=comment[:200] if comment else "",
                    custom_fields={'IP_Name': ip_name, 'Interface_Name': interface_name, 'IP_Type': ip_type},
                    tags=[{'name': IPV4_TAG if IP == "4" else IPV6_TAG}]
                )
            except Exception as e:
                print(f"Error creating interface or IP: {e}")

def create_ip_not_allocated(netbox, IP, target_site=None):
    """
    Create non-allocated IP addresses from Racktables in NetBox
    
    Args:
        netbox: NetBox client instance
        IP: "4" for IPv4 or "6" for IPv6
        target_site: Optional site name for filtering
    """
    print(f"Creating non-allocated IPv{IP} Addresses")
    
    # Get existing IPs to avoid duplicates
    existing_ips = set(ip['address'] for ip in netbox.ipam.get_ip_addresses())
    
    # Get IP names and comments
    with get_db_connection() as connection:
        with get_cursor(connection) as cursor:
            cursor.execute(f"SELECT ip,name,comment FROM IPv{IP}Address")
            ip_addresses = cursor.fetchall()
    
    for ip_data in ip_addresses:
        ip = ip_data["ip"]
        ip_name = ip_data["name"]
        comment = ip_data["comment"]
        
        # Format the IP address with CIDR notation
        string_ip = str(ipaddress.ip_address(ip)) + ("" if IP == "6" else "/32")
        
        # Skip if already exists
        if string_ip in existing_ips:
            continue
        
        # Create the IP address in NetBox
        try:
            netbox.ipam.create_ip_address(
                address=string_ip,
                description=comment[:200] if comment else "",
                custom_fields={'IP_Name': ip_name},
                tags=[{'name': IPV4_TAG if IP == "4" else IPV6_TAG}]
            )
            print(f"Created non-allocated IP {string_ip}")
        except Exception as e:
            print(f"Error creating IP {string_ip}: {e}")
