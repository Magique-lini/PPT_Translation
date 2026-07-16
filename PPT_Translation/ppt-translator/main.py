"""
main.py — FastAPI 后端服务

启动方式：
    pip install -r requirements.txt
    python main.py

访问：http://localhost:8000
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict

import aiofiles
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import (
    FILE_EXPIRE_HOURS,
    HOST,
    MAX_FILE_SIZE_MB,
    PORT,
    RESULT_DIR,
    UPLOAD_DIR,
)
from translator import translate_pptx

# ──────────────────────────────────────────────
# 初始化
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

Path(UPLOAD_DIR).mkdir(exist_ok=True)
Path(RESULT_DIR).mkdir(exist_ok=True)

app = FastAPI(title="PPT Translator API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 线程池（翻译是同步 IO 密集型，放线程池避免阻塞事件循环）
executor = ThreadPoolExecutor(max_workers=4)

# 内存任务存储  { task_id: {...} }
tasks: Dict[str, Any] = {}

DOMAINS = [
    {"value": "general",  "label": "通用"},
    {"value": "business", "label": "商务"},
    {"value": "medical",  "label": "医疗"},
    {"value": "legal",    "label": "法律"},
    {"value": "finance",  "label": "金融"},
    {"value": "academic", "label": "学术"},
    {"value": "tech",     "label": "科技 / IT"},
]


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────
def _cleanup_old_files() -> None:
    """启动时清理超过 FILE_EXPIRE_HOURS 的遗留文件"""
    expire_sec = FILE_EXPIRE_HOURS * 3600
    now = time.time()
    for d in (UPLOAD_DIR, RESULT_DIR):
        for f in Path(d).glob("*.pptx"):
            if now - f.stat().st_mtime > expire_sec:
                f.unlink(missing_ok=True)


def _run_translation(
    task_id: str,
    input_path: str,
    output_path: str,
    source_lang: str,
    target_lang: str,
    domain: str,
) -> None:
    """在线程池中执行翻译（同步）"""
    tasks[task_id]["status"] = "processing"
    logger.info(f"[{task_id}] 开始翻译 {source_lang}→{target_lang}，场景={domain}")

    def _progress(p: float) -> None:
        tasks[task_id]["progress"] = round(p, 2)

    try:
        translated_stem = translate_pptx(
            input_path, output_path, source_lang, target_lang, domain, _progress
        )
        tasks[task_id]["status"] = "completed"
        tasks[task_id]["progress"] = 1.0
        tasks[task_id]["translated_stem"] = translated_stem  # 存入译文文件名
        logger.info(f"[{task_id}] 翻译完成，文件名：{translated_stem}")
    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)
        logger.error(f"[{task_id}] 翻译失败：{e}")
    finally:
        # 删除上传的原始文件
        try:
            os.remove(input_path)
        except OSError:
            pass


# ──────────────────────────────────────────────
# API 路由
# ──────────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    _cleanup_old_files()
    logger.info(f"PPT Translator 已启动，访问 http://localhost:{PORT}")


@app.get("/v1/domains")
def get_domains():
    """返回支持的翻译场景列表"""
    return {"domains": DOMAINS}


@app.post("/v1/translate")
async def submit_translation(
    file: UploadFile = File(...),
    source_lang: str = Form(...),
    target_lang: str = Form(...),
    domain: str = Form("general"),
):
    """提交翻译任务，返回 task_id"""
    # 格式校验
    if not (file.filename or "").lower().endswith(".pptx"):
        raise HTTPException(status_code=400, detail="仅支持 .pptx 格式文件")
    if source_lang == target_lang:
        raise HTTPException(status_code=400, detail="源语言与目标语言不能相同")
    if source_lang not in ("zh", "en") or target_lang not in ("zh", "en"):
        raise HTTPException(status_code=400, detail="语言参数无效，仅支持 zh / en")

    # 读取并校验大小
    content = await file.read()
    if len(content) > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"文件大小超过 {MAX_FILE_SIZE_MB} MB 限制")

    task_id = str(uuid.uuid4())
    input_path = Path(UPLOAD_DIR) / f"{task_id}_in.pptx"
    output_path = Path(RESULT_DIR) / f"{task_id}_out.pptx"

    # 保存上传文件
    async with aiofiles.open(input_path, "wb") as f:
        await f.write(content)

    # 记录任务
    original_stem = Path(file.filename).stem
    tasks[task_id] = {
        "status": "queued",
        "progress": 0.0,
        "original_stem": original_stem,
        "output_path": str(output_path),
        "created_at": time.time(),
        "error": None,
    }

    # 在线程池中异步执行翻译
    loop = asyncio.get_event_loop()
    loop.run_in_executor(
        executor,
        _run_translation,
        task_id,
        str(input_path),
        str(output_path),
        source_lang,
        target_lang,
        domain,
    )

    return {
        "task_id": task_id,
        "status": "queued",
        "estimated_seconds": 30,
    }


@app.get("/v1/translate/{task_id}")
def get_task_status(task_id: str):
    """查询任务状态与进度"""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")

    task = tasks[task_id]
    status = task["status"]

    if status == "completed":
        return {
            "task_id": task_id,
            "status": "completed",
            "progress": 1.0,
            "download_url": f"/v1/translate/{task_id}/download",
        }
    elif status == "failed":
        return {
            "task_id": task_id,
            "status": "failed",
            "error": {"message": task.get("error", "未知错误")},
        }
    else:
        return {
            "task_id": task_id,
            "status": status,
            "progress": task["progress"],
        }


@app.get("/v1/translate/{task_id}/download")
def download_result(task_id: str):
    """下载翻译完成的 .pptx 文件"""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")

    task = tasks[task_id]
    if task["status"] != "completed":
        raise HTTPException(status_code=400, detail="翻译尚未完成")

    output_path = task["output_path"]
    if not os.path.exists(output_path):
        raise HTTPException(status_code=404, detail="文件已过期，请重新翻译")

    # 优先用译文文件名，如果翻译失败则回退到原文件名
    output_stem = task.get("translated_stem") or task["original_stem"]
    filename = f"{output_stem}_translated.pptx"
    return FileResponse(
        path=output_path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=filename,
    )


# ──────────────────────────────────────────────
# 静态文件（前端）放在最后，避免拦截 API 路由
# ──────────────────────────────────────────────
app.mount("/", StaticFiles(directory="static", html=True), name="static")


# ──────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
