from datetime import datetime, timedelta
import os
import asyncio
from vqc_monitor.db import repo
from vqc_monitor.core.config import settings
async def daily_cleanup():
    loop = asyncio.get_event_loop()
    while True:
        now = datetime.now()
        target_time = now.replace(hour=2, minute=0, second=0, microsecond=0)
        if now >= target_time:
            target_time += timedelta(days=1)
        wait_seconds = (target_time - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        await loop.run_in_executor(None, repo.clean_old_records, settings.RETENTION_DAYS)
        print(f"Daily cleanup executed at {datetime.now()}")