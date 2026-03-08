import logging

logger = logging.getLogger(__name__)

def get_store_key(store: dict) -> str:
    """Generates a unique key for a store based on name and address."""
    # Using lowercase and stripping to avoid minor formatting differences
    name = store.get('store_name', '').strip().lower()
    address = store.get('address', '').strip().lower()
    return f"{name}|{address}"

def compare_stores(current_stores: list, previous_stores: list) -> dict:
    """
    Compares two lists of stores and returns the delta.
    
    Returns a dictionary:
    {
        'opened': {
            'robo': [store_dict, ...],
            'non_robo': [store_dict, ...]
        },
        'closed': {
            'robo': [store_dict, ...],
            'non_robo': [store_dict, ...]
        },
        'net_change': int
    }
    """
    
    current_dict = {get_store_key(s): s for s in current_stores}
    previous_dict = {get_store_key(s): s for s in previous_stores}
    
    current_keys = set(current_dict.keys())
    previous_keys = set(previous_dict.keys())
    
    opened_keys = current_keys - previous_keys
    closed_keys = previous_keys - current_keys
    
    results = {
        'opened': {'robo': [], 'non_robo': []},
        'closed': {'robo': [], 'non_robo': []},
        'net_change': len(current_stores) - len(previous_stores)
    }
    
    for key in opened_keys:
        store = current_dict[key]
        if store.get('is_robo_shop'):
            results['opened']['robo'].append(store)
        else:
            results['opened']['non_robo'].append(store)
            
    for key in closed_keys:
        store = previous_dict[key]
        if store.get('is_robo_shop'):
            results['closed']['robo'].append(store)
        else:
            results['closed']['non_robo'].append(store)
            
    logger.info(f"Comparison complete: {len(opened_keys)} opened, {len(closed_keys)} closed. Net change: {results['net_change']}")
    
    return results

def format_comparison_for_email(comparison_results: dict) -> str:
    """Formats the comparison results into a readable string for the email body."""
    
    opened = comparison_results['opened']
    closed = comparison_results['closed']
    net_change = comparison_results['net_change']
    
    total_opened = len(opened['robo']) + len(opened['non_robo'])
    total_closed = len(closed['robo']) + len(closed['non_robo'])
    
    report = f"Popmart US Store Update\n"
    report += f"=======================\n\n"
    report += f"Net Openings: {net_change}\n"
    report += f"Total Stores Opened: {total_opened}\n"
    report += f"  - Regular Stores: {len(opened['non_robo'])}\n"
    report += f"  - ROBO SHOPs: {len(opened['robo'])}\n"
    report += f"Total Stores Closed: {total_closed}\n"
    report += f"  - Regular Stores: {len(closed['non_robo'])}\n"
    report += f"  - ROBO SHOPs: {len(closed['robo'])}\n\n"
    
    if total_opened > 0:
        report += f"--- NEWLY OPENED STORES ---\n"
        if opened['non_robo']:
            report += "Regular Stores:\n"
            for s in opened['non_robo']:
                report += f"  - {s['store_name']} ({s['address']})\n"
        if opened['robo']:
            report += "ROBO SHOPs:\n"
            for s in opened['robo']:
                report += f"  - {s['store_name']} ({s['address']})\n"
        report += "\n"
        
    if total_closed > 0:
        report += f"--- CLOSED STORES ---\n"
        if closed['non_robo']:
            report += "Regular Stores:\n"
            for s in closed['non_robo']:
                report += f"  - {s['store_name']} ({s['address']})\n"
        if closed['robo']:
            report += "ROBO SHOPs:\n"
            for s in closed['robo']:
                report += f"  - {s['store_name']} ({s['address']})\n"
        report += "\n"
        
    if total_opened == 0 and total_closed == 0:
        report += "No changes detected since the last update.\n"
        
    return report
