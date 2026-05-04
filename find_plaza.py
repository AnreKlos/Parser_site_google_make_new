import asyncio
from db.database import get_async_session
from db.models import Lead
from sqlalchemy import select

async def find_plaza():
    async with get_async_session() as session:
        result = await session.execute(select(Lead).where(Lead.name.ilike('%плаза%')))
        leads = result.all()
        for lead in leads:
            print(f"ID: {lead.id}, Name: {lead.name}, Address: {lead.address}")

if __name__ == "__main__":
    asyncio.run(find_plaza())
