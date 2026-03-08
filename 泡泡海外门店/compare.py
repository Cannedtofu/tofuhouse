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
    """Formats the comparison results into a readable string for the email body in Chinese."""
    
    opened = comparison_results['opened']
    closed = comparison_results['closed']
    net_change = comparison_results['net_change']
    
    opened_all = opened['robo'] + opened['non_robo']
    closed_all = closed['robo'] + closed['non_robo']
    
    total_opened = len(opened_all)
    total_closed = len(closed_all)
    
    # Calculate Region Breakdown
    opened_us = sum(1 for s in opened_all if s.get('region') == 'US')
    opened_eu = sum(1 for s in opened_all if s.get('region') == 'Europe')
    opened_unknown = total_opened - opened_us - opened_eu
    
    closed_us = sum(1 for s in closed_all if s.get('region') == 'US')
    closed_eu = sum(1 for s in closed_all if s.get('region') == 'Europe')
    closed_unknown = total_closed - closed_us - closed_eu
    
    report = f"泡泡玛特海外门店追踪更新\n"
    report += f"=======================\n\n"
    report += f"净新增门店: {net_change}\n"
    
    report += f"新开门店总数: {total_opened}\n"
    report += f"  - 常规实体店: {len(opened['non_robo'])}\n"
    report += f"  - 机器人商店 (ROBO SHOP): {len(opened['robo'])}\n"
    report += f"  [地区划分] 美国: {opened_us} | 欧洲: {opened_eu}"
    if opened_unknown > 0: report += f" | 未知: {opened_unknown}"
    report += "\n\n"
    
    report += f"关闭门店总数: {total_closed}\n"
    report += f"  - 常规实体店: {len(closed['non_robo'])}\n"
    report += f"  - 机器人商店 (ROBO SHOP): {len(closed['robo'])}\n"
    report += f"  [地区划分] 美国: {closed_us} | 欧洲: {closed_eu}"
    if closed_unknown > 0: report += f" | 未知: {closed_unknown}"
    report += "\n\n"
    
    if total_opened > 0:
        report += f"--- 新开门店列表 ---\n"
        if opened['non_robo']:
            report += "【常规实体店】:\n"
            for s in opened['non_robo']:
                region = s.get('region') or '未知'
                report += f"  - [{region}] {s['store_name']} ({s['address']})\n"
        if opened['robo']:
            report += "【机器人商店】:\n"
            for s in opened['robo']:
                region = s.get('region') or '未知'
                report += f"  - [{region}] {s['store_name']} ({s['address']})\n"
        report += "\n"
        
    if total_closed > 0:
        report += f"--- 已关闭门店列表 ---\n"
        if closed['non_robo']:
            report += "【常规实体店】:\n"
            for s in closed['non_robo']:
                region = s.get('region') or '未知'
                report += f"  - [{region}] {s['store_name']} ({s['address']})\n"
        if closed['robo']:
            report += "【机器人商店】:\n"
            for s in closed['robo']:
                region = s.get('region') or '未知'
                report += f"  - [{region}] {s['store_name']} ({s['address']})\n"
        report += "\n"
        
    if total_opened == 0 and total_closed == 0:
        report += "自上次更新以来，未检测到任何门店变更。\n"
        
    return report
