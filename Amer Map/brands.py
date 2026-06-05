BRANDS = [
    {
        "name": "arcteryx",
        "sheet_name": "Arc'teryx",
        "adapter": "locally",
        "base_url": "https://arcteryx.locally.com/stores/conversion_data",
        "company_id": "31",
        "dealers_company_id": "31",
        "referer": "https://arcteryx.locally.com/conversion?company_id=31&inline=1&lang=en",
        "requires_session": True,
        "regions": [
            # North America — from Arcteryx map.py (was commented out; now active)
            {"lat_min": 24.5, "lat_max": 83, "lng_min": -170, "lng_max": -52,
             "lat_step": 1.1751/2, "lng_step": 3.0185/2, "zoom": 8.4736},
            # Europe — from Arcteryx map.py (was the active region)
            {"lat_min": 34.5, "lat_max": 71, "lng_min": -10, "lng_max": 30,
             "lat_step": 0.871/2, "lng_step": 3.019/2, "zoom": 8.4736},
        ],
    },
    {
        "name": "hoka",
        "sheet_name": "Hoka",
        "adapter": "locally",
        "base_url": "https://hokaoneone.locally.com/stores/conversion_data",
        "company_id": "1428",
        "dealers_company_id": None,
        "referer": "https://www.hoka.com",
        "requires_session": False,
        "regions": [
            # North America — from Hoka map.py (was the active region)
            {"lat_min": 24.5, "lat_max": 83, "lng_min": -170, "lng_max": -52,
             "lat_step": 1.1751/2, "lng_step": 3.0185/2, "zoom": 8.4736},
            # Europe — from Hoka map.py (was commented out; now active)
            {"lat_min": 34.5, "lat_max": 71, "lng_min": -10, "lng_max": 30,
             "lat_step": 0.871/2, "lng_step": 3.019/2, "zoom": 8.4736},
        ],
    },
    {
        "name": "salomon",
        "sheet_name": "Salomon",
        "adapter": "locally",
        "base_url": "https://salomon.locally.com/stores/conversion_data",
        "company_id": "71",
        "dealers_company_id": None,
        "referer": "https://salomon.com/us/en/stores",
        "requires_session": False,
        "regions": [
            # North America — from Salomon map.py (was commented out; now active)
            {"lat_min": 24.5, "lat_max": 83, "lng_min": -170, "lng_max": -52,
             "lat_step": 1.1751/2, "lng_step": 3.0185/2, "zoom": 8.4736},
            # Europe — from Salomon map.py (was the active region)
            {"lat_min": 34.5, "lat_max": 71, "lng_min": -10, "lng_max": 30,
             "lat_step": 0.871/2, "lng_step": 3.019/2, "zoom": 8.4736},
        ],
    },
    {
        "name": "on_running",
        "sheet_name": "On Running",
        "adapter": "on_running",
        # Endpoint discovered via browser automation: form action on /en-us/dealers/ page
        # Response format: {"centerPosition": false, "dealers": [...]}
        "api_url": "https://customer-service.on-running.com/en-us/dealers/search",
        "api_params": {"all": "true"},
        "api_headers": {},
    },
]
