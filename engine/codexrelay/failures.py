"""Classify local task limits separately from remote billing and rate limits."""
import re


def classify_error(error):
    text = str(error or "")
    if "LOCAL_TOKEN_BUDGET" in text or "run token budget" in text:
        return {"code": "local_token_budget", "title": "本地任务 Token 预算已用完",
                "hint": "这不是账户余额不足。先让 Codex 核验已有修改，再继续剩余子任务；也可在供应商设置中提高单次任务预算。",
                "metrics": {name: int(value) for name, value in re.findall(r"\b(used|limit|next_input_estimate|history_compactions)=(\d+)", text)}}
    if "category=quota" in text or "status=402" in text:
        return {"code": "provider_quota", "title": "供应商额度或付款限制", "hint": "请检查供应商账户额度；提高本地任务预算不能解决这个问题。"}
    if "status=401" in text or "status=403" in text:
        return {"code": "provider_auth", "title": "供应商凭据或权限错误", "hint": "请检查 API Key 和模型访问权限。"}
    if "category=rate_limit" in text or "status=429" in text:
        return {"code": "provider_rate_limit", "title": "供应商请求频率受限", "hint": "稍后重试或检查供应商限流设置；已有修改需先核验。"}
    if "category=reasoning_history" in text:
        return {"code": "reasoning_history", "title": "模型历史格式不兼容", "hint": "请更新子代理引擎，核验已有改动后继续剩余工作。"}
    if "history budget" in text:
        return {"code": "history_limit", "title": "单次上下文过大", "hint": "减少一次读取的内容，保留聚焦的子任务；不要重复执行已完成的修改。"}
    if "time budget" in text:
        return {"code": "local_time_limit", "title": "本地任务运行时间达到上限", "hint": "检查已有结果，再继续剩余任务。"}
    if "output_truncated" in text:
        return {"code": "output_truncated", "title": "模型回复被截断", "hint": "单次模型输出达到上限；请缩小任务或增加单次输出上限。"}
    return {"code": "execution_failed", "title": "子代理执行未完成", "hint": "检查错误详情和已有文件变化，再决定后续处理。"}
