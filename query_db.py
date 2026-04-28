import asyncio
import os
import sys

# Добавляем путь к корню, чтобы импорты из api работали
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '.')))

from sqlalchemy import select, func
from api.app.db import engine
from api.app.models import (
    entities, entity_names, entity_types, fact_types, relation_types, 
    facts, relations, sources, source_fragments
)

async def main():
    try:
        async with engine.connect() as conn:
            tables = [
                ("Типы сущностей (entity_types)", entity_types),
                ("Типы фактов (fact_types)", fact_types),
                ("Типы связей (relation_types)", relation_types),
                ("Источники (sources)", sources),
                ("Фрагменты (source_fragments)", source_fragments),
                ("Сущности (entities)", entities),
                ("Имена (entity_names)", entity_names),
                ("Факты (facts)", facts),
                ("Связи (relations)", relations),
            ]
            print("=== ТАБЛИЦЫ И КОЛИЧЕСТВО ЗАПИСЕЙ ===")
            for name, table in tables:
                res = await conn.execute(select(func.count()).select_from(table))
                count = res.scalar()
                print(f"\n{name}: {count}")
                
                if count > 0:
                    print(f"  Примеры:")
                    res = await conn.execute(select(table).limit(2))
                    for row in res.mappings():
                        row_dict = dict(row)
                        # Усекаем длинные тексты для вывода
                        for k, v in row_dict.items():
                            if isinstance(v, str) and len(v) > 100:
                                row_dict[k] = v[:100] + "..."
                        print(f"    {row_dict}")
    finally:
        await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
