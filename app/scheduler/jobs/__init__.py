from datetime import datetime, timedelta

from apscheduler.triggers.cron import CronTrigger
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .spotify import poll_current_playback, poll_recently_played, set_scheduler


def register_jobs(scheduler: AsyncIOScheduler) -> None:
    # Set scheduler reference for self-scheduling
    set_scheduler(scheduler)

    # First poll runs immediately, then self-schedules
    scheduler.add_job(
        poll_current_playback,
        trigger="date",
        run_date=datetime.now() + timedelta(seconds=1),
        id="poll_current_playback",
        replace_existing=True,
    )

    scheduler.add_job(
        poll_recently_played,
        CronTrigger.from_crontab("0 * * * *"),  # Every hour at :00
        id="poll_recently_played",
        replace_existing=True,
    )

