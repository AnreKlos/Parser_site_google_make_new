#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import json
import re
from pathlib import Path
from typing import Dict, List, Any
from datetime import datetime

from db.database import get_async_session
from db.models import Lead
from sqlalchemy import select

from llm.critic import criticize_lead, safe_print

CONFIG_DIR = Path(r"D:\2 Clode Proj\1\neuralsync\src\configs")
OUTPUT_DIR = Path(__file__).parent / "data" / "critic_batch"
OUTPUT_FILE = OUTPUT_DIR / f"batch_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"


def find_all_configs() -> List[Dict[str, int]]:
    """Находит все config.js файлы и извлекает lead_id из имени."""
    configs = []
    pattern = re.compile(r'(.+)-(\d+)\.config\.js')
    
    if not CONFIG_DIR.exists():
        safe_print(f"❌ Директория с конфигами не найдена: {CONFIG_DIR}")
        return configs
    
    for file in CONFIG_DIR.glob("*.config.js"):
        match = pattern.match(file.name)
        if match:
            slug = match.group(1)
            lead_id = int(match.group(2))
            configs.append({"slug": slug, "lead_id": lead_id})
    
    return configs


async def run_batch() -> Dict[str, Any]:
    """Запускает критика для всех лидов с готовым config."""
    configs = find_all_configs()
    
    if not configs:
        safe_print("❌ Не найдено config файлов для оценки")
        return {"total": 0, "results": [], "stats": {}}
    
    safe_print(f"🎯 Найдено {len(configs)} config файлов")
    
    results = []
    ready_count = 0
    needs_review_count = 0
    rejected_count = 0
    
    for i, config in enumerate(configs, 1):
        lead_id = config["lead_id"]
        slug = config["slug"]
        
        safe_print(f"\n{'='*60}")
        safe_print(f"📋 [{i}/{len(configs)}] Обработка lead_id={lead_id}, slug={slug}")
        
        try:
            result = await criticize_lead(lead_id)
            if result:
                results.append({
                    "lead_id": lead_id,
                    "slug": slug,
                    "verdict": result.get("verdict"),
                    "score": result.get("score"),
                    "breakdown": result.get("scores_breakdown"),
                    "issues": result.get("issues", []),
                    "summary": result.get("summary")
                })
                
                # Статистика
                verdict = result.get("verdict")
                if verdict == "ready":
                    ready_count += 1
                elif verdict == "needs_review":
                    needs_review_count += 1
                elif verdict == "rejected":
                    rejected_count += 1
            else:
                safe_print(f"⚠️ Не удалось оценить lead_id={lead_id}")
        except Exception as e:
            safe_print(f"❌ Ошибка при оценке lead_id={lead_id}: {e}")
    
    # Статистика
    total = len(results)
    stats = {
        "total": total,
        "ready": ready_count,
        "needs_review": needs_review_count,
        "rejected": rejected_count,
        "ready_pct": round(ready_count / total * 100, 1) if total > 0 else 0,
        "needs_review_pct": round(needs_review_count / total * 100, 1) if total > 0 else 0,
        "rejected_pct": round(rejected_count / total * 100, 1) if total > 0 else 0
    }
    
    # Сохранение результатов
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_data = {
        "timestamp": datetime.now().isoformat(),
        "stats": stats,
        "results": results
    }
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    
    safe_print(f"\n{'='*60}")
    safe_print(f"✅ Batch оценка завершена")
    safe_print(f"📁 Результаты сохранены: {OUTPUT_FILE}")
    safe_print(f"📊 Статистика:")
    safe_print(f"   Всего: {stats['total']}")
    safe_print(f"   ready: {stats['ready']} ({stats['ready_pct']}%)")
    safe_print(f"   needs_review: {stats['needs_review']} ({stats['needs_review_pct']}%)")
    safe_print(f"   rejected: {stats['rejected']} ({stats['rejected_pct']}%)")
    
    # TOP-5 ready
    ready_results = [r for r in results if r["verdict"] == "ready"]
    if ready_results:
        ready_results.sort(key=lambda x: x["score"], reverse=True)
        safe_print(f"\n🏆 ТОП-5 READY:")
        for i, r in enumerate(ready_results[:5], 1):
            safe_print(f"   {i}. Lead {r['lead_id']}, score: {r['score']}")
    
    # TOP-5 rejected
    rejected_results = [r for r in results if r["verdict"] == "rejected"]
    if rejected_results:
        rejected_results.sort(key=lambda x: x["score"])
        safe_print(f"\n❌ ТОП-5 REJECTED:")
        for i, r in enumerate(rejected_results[:5], 1):
            safe_print(f"   {i}. Lead {r['lead_id']}, score: {r['score']}")
    
    return output_data


def main():
    result = asyncio.run(run_batch())
    print(f"\n{'='*60}")
    print(f"📁 Файл с результатами: {OUTPUT_FILE}")
    print(f"📊 Статистика: {result['stats']}")


if __name__ == "__main__":
    main()
