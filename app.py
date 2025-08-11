import os
import json
import uuid
import time
import base64
import sys
import inspect
import secrets
from loguru import logger
from pathlib import Path
from dotenv import load_dotenv

import requests
from flask import Flask, request, Response, jsonify, stream_with_context, render_template, redirect, session
from curl_cffi import requests as curl_requests
from werkzeug.middleware.proxy_fix import ProxyFix

# 加载 .env 文件
load_dotenv()

class Logger:
    def __init__(self, level="INFO", colorize=True, format=None):
        logger.remove()

        if format is None:
            format = (
                "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{extra[filename]}</cyan>:<cyan>{extra[function]}</cyan>:<cyan>{extra[lineno]}</cyan> | "
                "<level>{message}</level>"
            )

        logger.add(
            sys.stderr,
            level=level,
            format=format,
            colorize=colorize,
            backtrace=True,
            diagnose=True
        )

        self.logger = logger

    def _get_caller_info(self):
        frame = inspect.currentframe()
        try:
            caller_frame = frame.f_back.f_back
            full_path = caller_frame.f_code.co_filename
            function = caller_frame.f_code.co_name
            lineno = caller_frame.f_lineno

            filename = os.path.basename(full_path)

            return {
                'filename': filename,
                'function': function,
                'lineno': lineno
            }
        finally:
            del frame

    def info(self, message, source="API"):
        caller_info = self._get_caller_info()
        self.logger.bind(**caller_info).info(f"[{source}] {message}")

    def error(self, message, source="API"):
        caller_info = self._get_caller_info()

        if isinstance(message, Exception):
            self.logger.bind(**caller_info).exception(f"[{source}] {str(message)}")
        else:
            self.logger.bind(**caller_info).error(f"[{source}] {message}")

    def warning(self, message, source="API"):
        caller_info = self._get_caller_info()
        self.logger.bind(**caller_info).warning(f"[{source}] {message}")

    def debug(self, message, source="API"):
        caller_info = self._get_caller_info()
        self.logger.bind(**caller_info).debug(f"[{source}] {message}")

    async def request_logger(self, request):
        caller_info = self._get_caller_info()
        self.logger.bind(**caller_info).info(f"请求: {request.method} {request.path}", "Request")

logger = Logger(level="INFO")
DATA_DIR = Path("/data")

if not DATA_DIR.exists():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG = {
    "MODELS": {
        'grok-2': 'grok-latest',
        'grok-2-imageGen': 'grok-latest',
        'grok-2-search': 'grok-latest',
        "grok-3": "grok-3",
        "grok-3-search": "grok-3",
        "grok-3-imageGen": "grok-3",
        "grok-3-deepsearch": "grok-3",
        "grok-3-deepersearch": "grok-3",
        "grok-3-reasoning": "grok-3",
        "grok-4": "grok-4",
        "grok-4-free": "grok-4"
    },
    "API": {
        "IS_TEMP_CONVERSATION": os.environ.get("IS_TEMP_CONVERSATION", "true").lower() == "true",
        "IS_CUSTOM_SSO": os.environ.get("IS_CUSTOM_SSO", "false").lower() == "true",
        "BASE_URL": "https://grok.com",
        "API_KEY": os.environ.get("API_KEY", "sk-123456"),
        "SIGNATURE_COOKIE": None,
        "PICGO_KEY": os.environ.get("PICGO_KEY") or None,
        "TUMY_KEY": os.environ.get("TUMY_KEY") or None,
        "RETRY_TIME": 1000,
        "PROXY": os.environ.get("PROXY") or None
    },
    "ADMIN": {
        "MANAGER_SWITCH": os.environ.get("MANAGER_SWITCH") or None,
        "PASSWORD": os.environ.get("ADMINPASSWORD") or None 
    },
    "SERVER": {
        "COOKIE": None,
        "CF_CLEARANCE":os.environ.get("CF_CLEARANCE") or None,
        "PORT": int(os.environ.get("PORT", 5200))
    },
    "RETRY": {
        "RETRYSWITCH": False,
        "MAX_ATTEMPTS": 3
    },
    "TOKEN_STATUS_FILE": str(DATA_DIR / "token_status.json"),
    "SHOW_THINKING": os.environ.get("SHOW_THINKING") == "true",
    "IS_THINKING": False,
    "IS_IMG_GEN": False,
    "IS_IMG_GEN2": False,
    "ISSHOW_SEARCH_RESULTS": os.environ.get("ISSHOW_SEARCH_RESULTS", "true").lower() == "true"
}


DEFAULT_HEADERS = {
    'Accept': '*/*',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Accept-Encoding': 'gzip, deflate, br, zstd',
    'Content-Type': 'text/plain;charset=UTF-8',
    'Connection': 'keep-alive',
    'Origin': 'https://grok.com',
    'Referer': 'https://grok.com/',
    'Priority': 'u=1, i',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
    'Sec-Ch-Ua': '"Not(A:Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"',
    'Sec-Ch-Ua-Mobile': '?0',
    'Sec-Ch-Ua-Platform': '"Windows"',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
    'DNT': '1',
    'Upgrade-Insecure-Requests': '1',
    'Cache-Control': 'no-cache',
    'Pragma': 'no-cache'
}

class AuthTokenManager:
    def __init__(self):
        self.token_model_map = {}
        self.expired_tokens = set()
        self.token_status_map = {}
        self.pro_token_model_map = {}  # 专门用于grok-4的SSO_PRO令牌
        self.free_grok4_usage = {}  # 记录普通账号grok-4-free的每日使用情况
        self.load_daily_usage()  # 加载每日使用记录

        self.model_config = {
            "grok-2": {
                "RequestFrequency": 30,
                "ExpirationTime": 1 * 60 * 60 * 1000  # 1小时
            },
            "grok-3": {
                "RequestFrequency": 20,
                "ExpirationTime": 2 * 60 * 60 * 1000  # 2小时
            },
            "grok-3-deepsearch": {
                "RequestFrequency": 10,
                "ExpirationTime": 24 * 60 * 60 * 1000  # 24小时
            },
            "grok-3-deepersearch": {
                "RequestFrequency": 3,
                "ExpirationTime": 24 * 60 * 60 * 1000  # 24小时
            },
            "grok-3-reasoning": {
                "RequestFrequency": 10,
                "ExpirationTime": 24 * 60 * 60 * 1000  # 24小时
            },
            "grok-4": {
                "RequestFrequency": 20,
                "ExpirationTime": 2 * 60 * 60 * 1000  # 2小时
            },
            "grok-4-free": {
                "RequestFrequency": 10,
                "ExpirationTime": 24 * 60 * 60 * 1000  # 24小时
            }
        }
        self.token_reset_switch = False
        self.token_reset_timer = None
        self.load_token_status() # 加载令牌状态
    def save_token_status(self):
        try:        
            with open(CONFIG["TOKEN_STATUS_FILE"], 'w', encoding='utf-8') as f:
                json.dump(self.token_status_map, f, indent=2, ensure_ascii=False)
            logger.info("令牌状态已保存到配置文件", "TokenManager")
        except Exception as error:
            logger.error(f"保存令牌状态失败: {str(error)}", "TokenManager")
            
    def load_token_status(self):
        try:
            token_status_file = Path(CONFIG["TOKEN_STATUS_FILE"])
            if token_status_file.exists():
                with open(token_status_file, 'r', encoding='utf-8') as f:
                    self.token_status_map = json.load(f)
                logger.info("已从配置文件加载令牌状态", "TokenManager")
        except Exception as error:
            logger.error(f"加载令牌状态失败: {str(error)}", "TokenManager")
            
    def load_daily_usage(self):
        """加载每日使用记录"""
        try:
            daily_usage_file = Path(DATA_DIR / "daily_usage.json")
            if daily_usage_file.exists():
                with open(daily_usage_file, 'r', encoding='utf-8') as f:
                    self.free_grok4_usage = json.load(f)
                logger.info("已从配置文件加载每日使用记录", "TokenManager")
        except Exception as error:
            logger.error(f"加载每日使用记录失败: {str(error)}", "TokenManager")
            
    def save_daily_usage(self):
        """保存每日使用记录"""
        try:
            daily_usage_file = Path(DATA_DIR / "daily_usage.json")
            with open(daily_usage_file, 'w', encoding='utf-8') as f:
                json.dump(self.free_grok4_usage, f, indent=2, ensure_ascii=False)
            logger.debug("每日使用记录已保存", "TokenManager")
        except Exception as error:
            logger.error(f"保存每日使用记录失败: {str(error)}", "TokenManager")
            
    def get_today_key(self):
        """获取今日日期键"""
        import datetime
        return datetime.datetime.now().strftime("%Y-%m-%d")
        
    def check_and_update_daily_usage(self, model_id, is_return=False):
        """检查并更新每日使用次数"""
        if model_id != "grok-4-free":
            return True
            
        today = self.get_today_key()
        
        # 如果只是返回token而不实际使用，跳过检查
        if is_return:
            return True
            
        # 初始化今日记录
        if today not in self.free_grok4_usage:
            self.free_grok4_usage[today] = {}
            
        # 计算每日总限制 = 令牌数量 × 每个令牌10次
        token_count = len(self.token_model_map.get(model_id, []))
        daily_limit = token_count * 10
        
        # 获取今日总使用次数
        today_usage = sum(self.free_grok4_usage[today].values())
        
        if today_usage >= daily_limit:
            logger.warning(f"今日grok-4-free使用次数已达上限: {today_usage}/{daily_limit}", "TokenManager")
            return False
            
        # 更新使用记录（使用全局计数）
        global_key = "global"
        if global_key not in self.free_grok4_usage[today]:
            self.free_grok4_usage[today][global_key] = 0
        self.free_grok4_usage[today][global_key] += 1
        
        # 清理过期记录（保留最近7天）
        self.cleanup_old_usage_records()
        self.save_daily_usage()
        
        return True
        
    def cleanup_old_usage_records(self):
        """清理过期的使用记录"""
        import datetime
        today = datetime.datetime.now()
        keep_days = 7  # 保留7天的记录
        
        keys_to_remove = []
        for date_key in self.free_grok4_usage.keys():
            try:
                record_date = datetime.datetime.strptime(date_key, "%Y-%m-%d")
                if (today - record_date).days > keep_days:
                    keys_to_remove.append(date_key)
            except ValueError:
                # 日期格式错误的记录也删除
                keys_to_remove.append(date_key)
                
        for key in keys_to_remove:
            del self.free_grok4_usage[key]
    def add_token(self, token,isinitialization=False):
        sso = token.split("sso=")[1].split(";")[0]
        for model in self.model_config.keys():
            # grok-4 只给 SSO_PRO 令牌使用，普通令牌使用 grok-4-free
            if model == "grok-4":
                continue
                
            if model not in self.token_model_map:
                self.token_model_map[model] = []
            if sso not in self.token_status_map:
                self.token_status_map[sso] = {}

            existing_token_entry = next((entry for entry in self.token_model_map[model] if entry["token"] == token), None)

            if not existing_token_entry:
                self.token_model_map[model].append({
                    "token": token,
                    "RequestCount": 0,
                    "AddedTime": int(time.time() * 1000),
                    "StartCallTime": None
                })

                if model not in self.token_status_map[sso]:
                    self.token_status_map[sso][model] = {
                        "isValid": True,
                        "invalidatedTime": None,
                        "totalRequestCount": 0
                    }
        if not isinitialization:
            self.save_token_status()

    def add_pro_token(self, token, isinitialization=False):
        """专门处理SSO_PRO令牌，仅用于grok-4模型"""
        sso = token.split("sso=")[1].split(";")[0]
        model = "grok-4"
        
        if model not in self.pro_token_model_map:
            self.pro_token_model_map[model] = []
        if sso not in self.token_status_map:
            self.token_status_map[sso] = {}

        existing_token_entry = next((entry for entry in self.pro_token_model_map[model] if entry["token"] == token), None)

        if not existing_token_entry:
            self.pro_token_model_map[model].append({
                "token": token,
                "RequestCount": 0,
                "AddedTime": int(time.time() * 1000),
                "StartCallTime": None
            })

            if model not in self.token_status_map[sso]:
                self.token_status_map[sso][model] = {
                    "isValid": True,
                    "invalidatedTime": None,
                    "totalRequestCount": 0
                }
        if not isinitialization:
            self.save_token_status()

    def set_token(self, token):
        models = list(self.model_config.keys())
        self.token_model_map = {model: [{
            "token": token,
            "RequestCount": 0,
            "AddedTime": int(time.time() * 1000),
            "StartCallTime": None
        }] for model in models}

        sso = token.split("sso=")[1].split(";")[0]
        self.token_status_map[sso] = {model: {
            "isValid": True,
            "invalidatedTime": None,
            "totalRequestCount": 0
        } for model in models}

    def delete_token(self, token):
        try:
            sso = token.split("sso=")[1].split(";")[0]
            for model in self.token_model_map:
                self.token_model_map[model] = [entry for entry in self.token_model_map[model] if entry["token"] != token]

            if sso in self.token_status_map:
                del self.token_status_map[sso]
            
            self.save_token_status()

            logger.info(f"令牌已成功移除: {token}", "TokenManager")
            return True
        except Exception as error:
            logger.error(f"令牌删除失败: {str(error)}")
            return False
    def reduce_token_request_count(self, model_id, count):
        try:
            normalized_model = self.normalize_model_name(model_id)
            
            # grok-4 使用专门的SSO_PRO令牌
            if normalized_model == "grok-4":
                if normalized_model not in self.pro_token_model_map:
                    logger.error(f"模型 {normalized_model} 不存在于Pro令牌映射中", "TokenManager")
                    return False
                    
                if not self.pro_token_model_map[normalized_model]:
                    logger.error(f"模型 {normalized_model} 没有可用的Pro token", "TokenManager")
                    return False
                    
                token_entry = self.pro_token_model_map[normalized_model][0]
            else:
                if normalized_model not in self.token_model_map:
                    logger.error(f"模型 {normalized_model} 不存在", "TokenManager")
                    return False
                    
                if not self.token_model_map[normalized_model]:
                    logger.error(f"模型 {normalized_model} 没有可用的token", "TokenManager")
                    return False
                    
                token_entry = self.token_model_map[normalized_model][0]
            
            # 确保RequestCount不会小于0
            new_count = max(0, token_entry["RequestCount"] - count)
            reduction = token_entry["RequestCount"] - new_count
            
            token_entry["RequestCount"] = new_count
            
            # 如果是 grok-4-free，也需要减少每日使用计数
            if normalized_model == "grok-4-free":
                today = self.get_today_key()
                if today in self.free_grok4_usage:
                    global_key = "global"
                    if global_key in self.free_grok4_usage[today]:
                        self.free_grok4_usage[today][global_key] = max(
                            0, 
                            self.free_grok4_usage[today][global_key] - reduction
                        )
                        self.save_daily_usage()
            
            # 更新token状态
            if token_entry["token"]:
                sso = token_entry["token"].split("sso=")[1].split(";")[0]
                if sso in self.token_status_map and normalized_model in self.token_status_map[sso]:
                    self.token_status_map[sso][normalized_model]["totalRequestCount"] = max(
                        0, 
                        self.token_status_map[sso][normalized_model]["totalRequestCount"] - reduction
                    )
            return True
            
        except Exception as error:
            logger.error(f"重置校对token请求次数时发生错误: {str(error)}", "TokenManager")
            return False
    def get_next_token_for_model(self, model_id, is_return=False):
        normalized_model = self.normalize_model_name(model_id)

        # grok-4 使用专门的SSO_PRO令牌
        if normalized_model == "grok-4":
            if normalized_model not in self.pro_token_model_map or not self.pro_token_model_map[normalized_model]:
                return None
            
            token_entry = self.pro_token_model_map[normalized_model][0]
            if is_return:
                return token_entry["token"]

            if token_entry:
                if token_entry["StartCallTime"] is None:
                    token_entry["StartCallTime"] = int(time.time() * 1000)

                if not self.token_reset_switch:
                    self.start_token_reset_process()
                    self.token_reset_switch = True

                token_entry["RequestCount"] += 1

                if token_entry["RequestCount"] > self.model_config[normalized_model]["RequestFrequency"]:
                    self.remove_pro_token_from_model(normalized_model, token_entry["token"])
                    next_token_entry = self.pro_token_model_map[normalized_model][0] if self.pro_token_model_map[normalized_model] else None
                    return next_token_entry["token"] if next_token_entry else None

                sso = token_entry["token"].split("sso=")[1].split(";")[0]
                if sso in self.token_status_map and normalized_model in self.token_status_map[sso]:
                    if token_entry["RequestCount"] == self.model_config[normalized_model]["RequestFrequency"]:
                        self.token_status_map[sso][normalized_model]["isValid"] = False
                        self.token_status_map[sso][normalized_model]["invalidatedTime"] = int(time.time() * 1000)
                    self.token_status_map[sso][normalized_model]["totalRequestCount"] += 1

                    self.save_token_status()

                return token_entry["token"]
        elif normalized_model == "grok-4-free":
            # grok-4-free 使用普通SSO令牌，但需要检查每日使用限制
            if normalized_model not in self.token_model_map or not self.token_model_map[normalized_model]:
                return None

            # 检查今日是否已达到使用限制
            if not self.check_and_update_daily_usage(normalized_model, is_return):
                return None

            token_entry = self.token_model_map[normalized_model][0]
            if is_return:
                return token_entry["token"]

            if token_entry:
                if token_entry["StartCallTime"] is None:
                    token_entry["StartCallTime"] = int(time.time() * 1000)

                if not self.token_reset_switch:
                    self.start_token_reset_process()
                    self.token_reset_switch = True

                token_entry["RequestCount"] += 1

                if token_entry["RequestCount"] > self.model_config[normalized_model]["RequestFrequency"]:
                    self.remove_token_from_model(normalized_model, token_entry["token"])
                    next_token_entry = self.token_model_map[normalized_model][0] if self.token_model_map[normalized_model] else None
                    return next_token_entry["token"] if next_token_entry else None

                sso = token_entry["token"].split("sso=")[1].split(";")[0]
                if sso in self.token_status_map and normalized_model in self.token_status_map[sso]:
                    if token_entry["RequestCount"] == self.model_config[normalized_model]["RequestFrequency"]:
                        self.token_status_map[sso][normalized_model]["isValid"] = False
                        self.token_status_map[sso][normalized_model]["invalidatedTime"] = int(time.time() * 1000)
                    self.token_status_map[sso][normalized_model]["totalRequestCount"] += 1

                    self.save_token_status()

                return token_entry["token"]
        else:
            # 其他模型使用普通的SSO令牌
            if normalized_model not in self.token_model_map or not self.token_model_map[normalized_model]:
                return None

            token_entry = self.token_model_map[normalized_model][0]
            if is_return:
                return token_entry["token"]

            if token_entry:
                if token_entry["StartCallTime"] is None:
                    token_entry["StartCallTime"] = int(time.time() * 1000)

                if not self.token_reset_switch:
                    self.start_token_reset_process()
                    self.token_reset_switch = True

                token_entry["RequestCount"] += 1

                if token_entry["RequestCount"] > self.model_config[normalized_model]["RequestFrequency"]:
                    self.remove_token_from_model(normalized_model, token_entry["token"])
                    next_token_entry = self.token_model_map[normalized_model][0] if self.token_model_map[normalized_model] else None
                    return next_token_entry["token"] if next_token_entry else None

                sso = token_entry["token"].split("sso=")[1].split(";")[0]
                if sso in self.token_status_map and normalized_model in self.token_status_map[sso]:
                    if token_entry["RequestCount"] == self.model_config[normalized_model]["RequestFrequency"]:
                        self.token_status_map[sso][normalized_model]["isValid"] = False
                        self.token_status_map[sso][normalized_model]["invalidatedTime"] = int(time.time() * 1000)
                    self.token_status_map[sso][normalized_model]["totalRequestCount"] += 1

                    self.save_token_status()

                return token_entry["token"]

        return None

    def remove_token_from_model(self, model_id, token):
        normalized_model = self.normalize_model_name(model_id)

        if normalized_model not in self.token_model_map:
            logger.error(f"模型 {normalized_model} 不存在", "TokenManager")
            return False

        model_tokens = self.token_model_map[normalized_model]
        token_index = next((i for i, entry in enumerate(model_tokens) if entry["token"] == token), -1)

        if token_index != -1:
            removed_token_entry = model_tokens.pop(token_index)
            self.expired_tokens.add((
                removed_token_entry["token"],
                normalized_model,
                int(time.time() * 1000)
            ))

            if not self.token_reset_switch:
                self.start_token_reset_process()
                self.token_reset_switch = True

            logger.info(f"模型{model_id}的令牌已失效，已成功移除令牌: {token}", "TokenManager")
            return True

        logger.error(f"在模型 {normalized_model} 中未找到 token: {token}", "TokenManager")
        return False

    def remove_pro_token_from_model(self, model_id, token):
        """专门用于移除grok-4的SSO_PRO令牌"""
        normalized_model = self.normalize_model_name(model_id)

        if normalized_model not in self.pro_token_model_map:
            logger.error(f"模型 {normalized_model} 不存在于Pro令牌映射中", "TokenManager")
            return False

        model_tokens = self.pro_token_model_map[normalized_model]
        token_index = next((i for i, entry in enumerate(model_tokens) if entry["token"] == token), -1)

        if token_index != -1:
            removed_token_entry = model_tokens.pop(token_index)
            self.expired_tokens.add((
                removed_token_entry["token"],
                normalized_model,
                int(time.time() * 1000)
            ))

            if not self.token_reset_switch:
                self.start_token_reset_process()
                self.token_reset_switch = True

            logger.info(f"模型{model_id}的Pro令牌已失效，已成功移除令牌: {token}", "TokenManager")
            return True

        logger.error(f"在模型 {normalized_model} 的Pro令牌映射中未找到 token: {token}", "TokenManager")
        return False

    def get_expired_tokens(self):
        return list(self.expired_tokens)

    def normalize_model_name(self, model):
        if model.startswith('grok-') and 'deepsearch' not in model and 'reasoning' not in model and 'free' not in model:
            return '-'.join(model.split('-')[:2])
        return model

    def get_token_count_for_model(self, model_id):
        normalized_model = self.normalize_model_name(model_id)
        if normalized_model == "grok-4":
            return len(self.pro_token_model_map.get(normalized_model, []))
        else:
            return len(self.token_model_map.get(normalized_model, []))

    def get_remaining_token_request_capacity(self):
        remaining_capacity_map = {}

        for model in self.model_config.keys():
            if model == "grok-4":
                model_tokens = self.pro_token_model_map.get(model, [])
            elif model == "grok-4-free":
                # grok-4-free 需要考虑每日限制
                model_tokens = self.token_model_map.get(model, [])
                today = self.get_today_key()
                today_usage = sum(self.free_grok4_usage.get(today, {}).values())
                
                # 每日限制 = 令牌数量 × 每个令牌10次
                daily_limit = len(model_tokens) * 10
                daily_remaining = max(0, daily_limit - today_usage)
                
                model_request_frequency = self.model_config[model]["RequestFrequency"]
                total_used_requests = sum(token_entry.get("RequestCount", 0) for token_entry in model_tokens)
                token_remaining = (len(model_tokens) * model_request_frequency) - total_used_requests
                
                # 返回两者的最小值
                remaining_capacity_map[model] = max(0, min(token_remaining, daily_remaining))
                continue
            else:
                model_tokens = self.token_model_map.get(model, [])
            
            model_request_frequency = self.model_config[model]["RequestFrequency"]

            total_used_requests = sum(token_entry.get("RequestCount", 0) for token_entry in model_tokens)

            remaining_capacity = (len(model_tokens) * model_request_frequency) - total_used_requests
            remaining_capacity_map[model] = max(0, remaining_capacity)

        return remaining_capacity_map

    def get_token_array_for_model(self, model_id):
        normalized_model = self.normalize_model_name(model_id)
        if normalized_model == "grok-4":
            return self.pro_token_model_map.get(normalized_model, [])
        else:
            return self.token_model_map.get(normalized_model, [])

    def start_token_reset_process(self):
        def reset_expired_tokens():
            now = int(time.time() * 1000)

            tokens_to_remove = set()
            for token_info in self.expired_tokens:
                token, model, expired_time = token_info
                expiration_time = self.model_config[model]["ExpirationTime"]

                if now - expired_time >= expiration_time:
                    if not any(entry["token"] == token for entry in self.token_model_map.get(model, [])):
                        if model not in self.token_model_map:
                            self.token_model_map[model] = []

                        self.token_model_map[model].append({
                            "token": token,
                            "RequestCount": 0,
                            "AddedTime": now,
                            "StartCallTime": None
                        })

                    sso = token.split("sso=")[1].split(";")[0]
                    if sso in self.token_status_map and model in self.token_status_map[sso]:
                        self.token_status_map[sso][model]["isValid"] = True
                        self.token_status_map[sso][model]["invalidatedTime"] = None
                        self.token_status_map[sso][model]["totalRequestCount"] = 0

                    tokens_to_remove.add(token_info)

            self.expired_tokens -= tokens_to_remove

            for model in self.model_config.keys():
                if model not in self.token_model_map:
                    continue

                for token_entry in self.token_model_map[model]:
                    if not token_entry.get("StartCallTime"):
                        continue

                    expiration_time = self.model_config[model]["ExpirationTime"]
                    if now - token_entry["StartCallTime"] >= expiration_time:
                        sso = token_entry["token"].split("sso=")[1].split(";")[0]
                        if sso in self.token_status_map and model in self.token_status_map[sso]:
                            self.token_status_map[sso][model]["isValid"] = True
                            self.token_status_map[sso][model]["invalidatedTime"] = None
                            self.token_status_map[sso][model]["totalRequestCount"] = 0

                        token_entry["RequestCount"] = 0
                        token_entry["StartCallTime"] = None

        import threading
        # 启动一个线程执行定时任务，每小时执行一次
        def run_timer():
            while True:
                reset_expired_tokens()
                time.sleep(3600)

        timer_thread = threading.Thread(target=run_timer)
        timer_thread.daemon = True
        timer_thread.start()

    def get_all_tokens(self):
        all_tokens = set()
        for model_tokens in self.token_model_map.values():
            for entry in model_tokens:
                all_tokens.add(entry["token"])
        return list(all_tokens)
    def get_current_token(self, model_id):
        normalized_model = self.normalize_model_name(model_id)

        if normalized_model == "grok-4":
            if normalized_model not in self.pro_token_model_map or not self.pro_token_model_map[normalized_model]:
                return None
            token_entry = self.pro_token_model_map[normalized_model][0]
        else:
            if normalized_model not in self.token_model_map or not self.token_model_map[normalized_model]:
                return None
            token_entry = self.token_model_map[normalized_model][0]

        return token_entry["token"]

    def get_token_status_map(self):
        return self.token_status_map

    def remove_token_for_model(self, model_id, token):
        """通用的令牌移除方法，根据模型类型选择合适的移除方式"""
        normalized_model = self.normalize_model_name(model_id)
        if normalized_model == "grok-4":
            return self.remove_pro_token_from_model(model_id, token)
        else:
            return self.remove_token_from_model(model_id, token)

class Utils:
    # 代理池配置
    _proxy_pool = []
    _proxy_index = 0
    _proxy_lock = None

    @staticmethod
    def is_network_error(error):
        """判断是否为网络连接错误，这类错误不应该移除令牌"""
        error_str = str(error).lower()
        network_error_keywords = [
            'curl: (18)',  # curl数据传输过早结束
            'curl: (6)',   # 无法解析主机
            'curl: (7)',   # 连接失败
            'curl: (28)',  # 操作超时
            'curl: (35)',  # SSL连接错误
            'curl: (52)',  # 服务器返回空回复
            'curl: (56)',  # 接收网络数据失败
            'connection timeout',
            'connection reset',
            'network unreachable',
            'timeout',
            'connection refused',
            'connection aborted'
        ]
        return any(keyword in error_str for keyword in network_error_keywords)
    
    @staticmethod
    def init_proxy_pool():
        """初始化代理池"""
        import threading
        Utils._proxy_lock = threading.Lock()
        
        proxy_env = os.environ.get("PROXY")
        if proxy_env:
            if ',' in proxy_env:
                # 多个代理，逗号分隔
                proxies = [p.strip() for p in proxy_env.split(',') if p.strip()]
                Utils._proxy_pool = [f"http://{proxy}" if not proxy.startswith(('http://', 'https://', 'socks5://')) else proxy for proxy in proxies]
            else:
                # 单个代理
                proxy = proxy_env.strip()
                if not proxy.startswith(('http://', 'https://', 'socks5://')):
                    proxy = f"http://{proxy}"
                Utils._proxy_pool = [proxy]
        
        logger.info(f"代理池已初始化，共 {len(Utils._proxy_pool)} 个代理", "ProxyPool")
        for i, proxy in enumerate(Utils._proxy_pool):
            # 只显示前20个字符，保护敏感信息
            masked_proxy = proxy[:20] + "..." if len(proxy) > 20 else proxy
            logger.info(f"代理 {i+1}: {masked_proxy}", "ProxyPool")
    
    @staticmethod
    def get_next_proxy():
        """获取下一个代理，实现轮换"""
        if not Utils._proxy_pool:
            return None
            
        with Utils._proxy_lock:
            current_index = Utils._proxy_index
            proxy = Utils._proxy_pool[current_index]
            Utils._proxy_index = (Utils._proxy_index + 1) % len(Utils._proxy_pool)
            logger.info(f"使用代理 {current_index + 1}/{len(Utils._proxy_pool)}: {proxy[:30]}...", "ProxyPool")
            return proxy

    @staticmethod
    def organize_search_results(search_results):
        if not search_results or 'results' not in search_results:
            return ''

        results = search_results['results']
        formatted_results = []

        for index, result in enumerate(results):
            title = result.get('title', '未知标题')
            url = result.get('url', '#')
            preview = result.get('preview', '无预览内容')

            formatted_result = f"\r\n<details><summary>资料[{index}]: {title}</summary>\r\n{preview}\r\n\n[Link]({url})\r\n</details>"
            formatted_results.append(formatted_result)

        return '\n\n'.join(formatted_results)

    @staticmethod
    def create_auth_headers(model, is_return=False):
        return token_manager.get_next_token_for_model(model, is_return)

    @staticmethod
    def get_proxy_options():
        proxy = Utils.get_next_proxy()
        proxy_options = {}

        if proxy:
            if proxy.startswith("socks5://"):
                # 对于 curl_cffi，SOCKS5 代理使用 proxy 参数
                proxy_options["proxy"] = proxy
                # 对于 requests 库，需要使用 proxies 参数，但 requests 不直接支持 SOCKS5
                # 这里只设置 proxy 参数给 curl_cffi 使用
            else:
                # HTTP/HTTPS 代理，同时支持 requests 和 curl_cffi
                proxy_options["proxies"] = {"https": proxy, "http": proxy}     
        return proxy_options

    @staticmethod
    def get_proxy_options_for_requests():
        """专门为 requests 库返回代理配置"""
        proxy = Utils.get_next_proxy()
        proxy_options = {}

        if proxy and not proxy.startswith("socks5://"):
            # requests 库只支持 HTTP/HTTPS 代理
            proxy_options["proxies"] = {"https": proxy, "http": proxy}
        return proxy_options

    @staticmethod
    def generate_xai_request_id():
        """生成 x-xai-request-id UUID"""
        return str(uuid.uuid4())

    @staticmethod
    def get_statsig_id():
        """从外部接口获取 x-statsig-id"""
        try:
            proxy_options = Utils.get_proxy_options_for_requests()
            response = requests.get(
                "https://rui.soundai.ee/x.php",
                timeout=10,
                **proxy_options
            )
            
            if response.status_code == 200:
                result = response.json()
                statsig_id = result.get("x_statsig_id", "")
                if statsig_id:
                    logger.info(f"成功获取 x-statsig-id: {statsig_id[:20]}...", "Server")
                    return statsig_id
                else:
                    logger.error("返回的 x-statsig-id 为空", "Server")
                    return None
            else:
                logger.error(f"获取 x-statsig-id 失败，状态码: {response.status_code}", "Server")
                return None
        except Exception as error:
            logger.error(f"获取 x-statsig-id 异常: {str(error)}", "Server")
            return None

class GrokApiClient:
    def __init__(self, model_id):
        if model_id not in CONFIG["MODELS"]:
            raise ValueError(f"不支持的模型: {model_id}")
        self.model_id = CONFIG["MODELS"][model_id]

    def process_message_content(self, content):
        if isinstance(content, str):
            return content
        return None

    def get_image_type(self, base64_string):
        mime_type = 'image/jpeg'
        if 'data:image' in base64_string:
            import re
            matches = re.search(r'data:([a-zA-Z0-9]+\/[a-zA-Z0-9-.+]+);base64,', base64_string)
            if matches:
                mime_type = matches.group(1)

        extension = mime_type.split('/')[1]
        file_name = f"image.{extension}"

        return {
            "mimeType": mime_type,
            "fileName": file_name
        }
    def upload_base64_file(self, message, model):
        try:
            message_base64 = base64.b64encode(message.encode('utf-8')).decode('utf-8')
            upload_data = {
                "fileName": "message.txt",
                "fileMimeType": "text/plain",
                "content": message_base64
            }

            logger.info("发送文字文件请求", "Server")
            cookie = f"{Utils.create_auth_headers(model, True)};{CONFIG['SERVER']['CF_CLEARANCE']}" 
            proxy_options = Utils.get_proxy_options()
            response = curl_requests.post(
                "https://grok.com/rest/app-chat/upload-file",
                headers={
                    **DEFAULT_HEADERS,
                    "Cookie":cookie
                },
                json=upload_data,
                impersonate="chrome133a",
                **proxy_options
            )

            if response.status_code != 200:
                logger.error(f"上传文件失败,状态码:{response.status_code}", "Server")
                raise Exception(f"上传文件失败,状态码:{response.status_code}")

            result = response.json()
            logger.info(f"上传文件成功: {result}", "Server")
            return result.get("fileMetadataId", "")

        except Exception as error:
            logger.error(str(error), "Server")
            raise Exception(f"上传文件失败,状态码:{response.status_code}")
    def upload_base64_image(self, base64_data, url):
        try:
            if 'data:image' in base64_data:
                image_buffer = base64_data.split(',')[1]
            else:
                image_buffer = base64_data

            image_info = self.get_image_type(base64_data)
            mime_type = image_info["mimeType"]
            file_name = image_info["fileName"]

            upload_data = {
                "rpc": "uploadFile",
                "req": {
                    "fileName": file_name,
                    "fileMimeType": mime_type,
                    "content": image_buffer
                }
            }

            logger.info("发送图片请求", "Server")

            proxy_options = Utils.get_proxy_options()
            response = curl_requests.post(
                url,
                headers={
                    **DEFAULT_HEADERS,
                    "Cookie":CONFIG["SERVER"]['COOKIE']
                },
                json=upload_data,
                impersonate="chrome133a",
                **proxy_options
            )

            if response.status_code != 200:
                logger.error(f"上传图片失败,状态码:{response.status_code}", "Server")
                return ''

            result = response.json()
            logger.info(f"上传图片成功: {result}", "Server")
            return result.get("fileMetadataId", "")

        except Exception as error:
            logger.error(str(error), "Server")
            return ''
    # def convert_system_messages(self, messages):
    #     try:
    #         system_prompt = []
    #         i = 0
    #         while i < len(messages):
    #             if messages[i].get('role') != 'system':
    #                 break

    #             system_prompt.append(self.process_message_content(messages[i].get('content')))
    #             i += 1

    #         messages = messages[i:]
    #         system_prompt = '\n'.join(system_prompt)

    #         if not messages:
    #             raise ValueError("没有找到用户或者AI消息")
    #         return {"system_prompt":system_prompt,"messages":messages}
    #     except Exception as error:
    #         logger.error(str(error), "Server")
    #         raise ValueError(error)
    def prepare_chat_request(self, request):
        if ((request["model"] == 'grok-2-imageGen' or request["model"] == 'grok-3-imageGen') and
            not CONFIG["API"]["PICGO_KEY"] and not CONFIG["API"]["TUMY_KEY"] and
            request.get("stream", False)):
            raise ValueError("该模型流式输出需要配置PICGO或者TUMY图床密钥!")

        # system_message, todo_messages = self.convert_system_messages(request["messages"]).values()
        todo_messages = request["messages"]
        if request["model"] in ['grok-2-imageGen', 'grok-3-imageGen', 'grok-3-deepsearch']:
            last_message = todo_messages[-1]
            if last_message["role"] != 'user':
                raise ValueError('此模型最后一条消息必须是用户消息!')
            todo_messages = [last_message]
        file_attachments = []
        messages = ''
        last_role = None
        last_content = ''
        message_length = 0
        convert_to_file = False
        last_message_content = ''
        search = request["model"] in ['grok-2-search', 'grok-3-search']
        deepsearchPreset = ''
        if request["model"] == 'grok-3-deepsearch':
            deepsearchPreset = 'default'
        elif request["model"] == 'grok-3-deepersearch':
            deepsearchPreset = 'deeper'

        # 移除<think>标签及其内容和base64图片
        def remove_think_tags(text):
            import re
            text = re.sub(r'<think>[\s\S]*?<\/think>', '', text).strip()
            text = re.sub(r'!\[image\]\(data:.*?base64,.*?\)', '[图片]', text)
            return text

        def process_content(content):
            if isinstance(content, list):
                text_content = ''
                for item in content:
                    if item["type"] == 'image_url':
                        text_content += ("[图片]" if not text_content else '\n[图片]')
                    elif item["type"] == 'text':
                        text_content += (remove_think_tags(item["text"]) if not text_content else '\n' + remove_think_tags(item["text"]))
                return text_content
            elif isinstance(content, dict) and content is not None:
                if content["type"] == 'image_url':
                    return "[图片]"
                elif content["type"] == 'text':
                    return remove_think_tags(content["text"])
            return remove_think_tags(self.process_message_content(content))
        for current in todo_messages:
            role = 'assistant' if current["role"] == 'assistant' else 'user'
            is_last_message = current == todo_messages[-1]

            if is_last_message and "content" in current:
                if isinstance(current["content"], list):
                    for item in current["content"]:
                        if item["type"] == 'image_url':
                            processed_image = self.upload_base64_image(
                                item["image_url"]["url"],
                                f"{CONFIG['API']['BASE_URL']}/api/rpc"
                            )
                            if processed_image:
                                file_attachments.append(processed_image)
                elif isinstance(current["content"], dict) and current["content"].get("type") == 'image_url':
                    processed_image = self.upload_base64_image(
                        current["content"]["image_url"]["url"],
                        f"{CONFIG['API']['BASE_URL']}/api/rpc"
                    )
                    if processed_image:
                        file_attachments.append(processed_image)


            text_content = process_content(current.get("content", ""))
            if is_last_message and convert_to_file:
                last_message_content = f"{role.upper()}: {text_content or '[图片]'}\n"
                continue
            if text_content or (is_last_message and file_attachments):
                if role == last_role and text_content:
                    last_content += '\n' + text_content
                    messages = messages[:messages.rindex(f"{role.upper()}: ")] + f"{role.upper()}: {last_content}\n"
                else:
                    messages += f"{role.upper()}: {text_content or '[图片]'}\n"
                    last_content = text_content
                    last_role = role
            message_length += len(messages)
            if message_length >= 40000:
                convert_to_file = True
               
        if convert_to_file:
            file_id = self.upload_base64_file(messages, request["model"])
            if file_id:
                file_attachments.insert(0, file_id)
            messages = last_message_content.strip()
        if messages.strip() == '':
            if convert_to_file:
                messages = '基于txt文件内容进行回复：'
            else:
                raise ValueError('消息内容为空!')
        return {
            "temporary": CONFIG["API"].get("IS_TEMP_CONVERSATION", False),
            "modelName": self.model_id,
            "message": messages.strip(),
            "fileAttachments": file_attachments[:4],
            "imageAttachments": [],
            "disableSearch": False,
            "enableImageGeneration": True,
            "returnImageBytes": False,
            "returnRawGrokInXaiRequest": False,
            "enableImageStreaming": False,
            "imageGenerationCount": 1,
            "forceConcise": False,
            "toolOverrides": {
                "imageGen": request["model"] in ['grok-2-imageGen', 'grok-3-imageGen'],
                "webSearch": search,
                "xSearch": search,
                "xMediaSearch": search,
                "trendsSearch": search,
                "xPostAnalyze": search
            },
            "enableSideBySide": True,
            "sendFinalMetadata": True,
            "customPersonality": "",
            "deepsearchPreset": deepsearchPreset,
            "isReasoning": request["model"] == 'grok-3-reasoning',
            "disableTextFollowUps": True
        }

class MessageProcessor:
    @staticmethod
    def create_chat_response(message, model, is_stream=False):
        base_response = {
            "id": f"chatcmpl-{uuid.uuid4()}",
            "created": int(time.time()),
            "model": model
        }

        if is_stream:
            return {
                **base_response,
                "object": "chat.completion.chunk",
                "choices": [{
                    "index": 0,
                    "delta": {
                        "content": message
                    }
                }]
            }

        return {
            **base_response,
            "object": "chat.completion",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": message
                },
                "finish_reason": "stop"
            }],
            "usage": None
        }

def process_model_response(response, model):
    result = {"token": None, "imageUrl": None}

    if CONFIG["IS_IMG_GEN"]:
        if response.get("cachedImageGenerationResponse") and not CONFIG["IS_IMG_GEN2"]:
            result["imageUrl"] = response["cachedImageGenerationResponse"]["imageUrl"]
        return result

    if model == 'grok-2':
        result["token"] = response.get("token")
    elif model in ['grok-2-search', 'grok-3-search']:
        if response.get("webSearchResults") and CONFIG["ISSHOW_SEARCH_RESULTS"]:
            result["token"] = f"\r\n<think>{Utils.organize_search_results(response['webSearchResults'])}</think>\r\n"
        else:
            result["token"] = response.get("token")
    elif model == 'grok-3':
        result["token"] = response.get("token")
    elif model in ['grok-3-deepsearch', 'grok-3-deepersearch']:
        if response.get("messageStepId") and not CONFIG["SHOW_THINKING"]:
            return result
        if response.get("messageStepId") and not CONFIG["IS_THINKING"]:
            result["token"] = "<think>" + response.get("token", "")
            CONFIG["IS_THINKING"] = True
        elif not response.get("messageStepId") and CONFIG["IS_THINKING"] and response.get("messageTag") == "final":
            result["token"] = "</think>" + response.get("token", "")
            CONFIG["IS_THINKING"] = False
        elif (response.get("messageStepId") and CONFIG["IS_THINKING"] and response.get("messageTag") == "assistant") or response.get("messageTag") == "final":
            result["token"] = response.get("token","")
        elif (CONFIG["IS_THINKING"] and response.get("token","").get("action","") == "webSearch"):
            result["token"] = response.get("token","").get("action_input","").get("query","")            
        elif (CONFIG["IS_THINKING"] and response.get("webSearchResults")):
            result["token"] = Utils.organize_search_results(response['webSearchResults'])
    elif model == 'grok-3-reasoning':
        if response.get("isThinking") and not CONFIG["SHOW_THINKING"]:
            return result

        if response.get("isThinking") and not CONFIG["IS_THINKING"]:
            result["token"] = "<think>" + response.get("token", "")
            CONFIG["IS_THINKING"] = True
        elif not response.get("isThinking") and CONFIG["IS_THINKING"]:
            result["token"] = "</think>" + response.get("token", "")
            CONFIG["IS_THINKING"] = False
        else:
            result["token"] = response.get("token")
    elif model == 'grok-4':
        result["token"] = response.get("token")
    elif model == 'grok-4-free':
        result["token"] = response.get("token")

    return result

def handle_image_response(image_url):
    max_retries = 2
    retry_count = 0
    image_base64_response = None

    while retry_count < max_retries:
        try:
            proxy_options = Utils.get_proxy_options()
            image_base64_response = curl_requests.get(
                f"https://assets.grok.com/{image_url}",
                headers={
                    **DEFAULT_HEADERS,
                    "Cookie":CONFIG["SERVER"]['COOKIE']
                },
                impersonate="chrome133a",
                **proxy_options
            )

            if image_base64_response.status_code == 200:
                break

            retry_count += 1
            if retry_count == max_retries:
                raise Exception(f"上游服务请求失败! status: {image_base64_response.status_code}")

            time.sleep(CONFIG["API"]["RETRY_TIME"] / 1000 * retry_count)

        except Exception as error:
            logger.error(str(error), "Server")
            retry_count += 1
            if retry_count == max_retries:
                raise

            time.sleep(CONFIG["API"]["RETRY_TIME"] / 1000 * retry_count)

    image_buffer = image_base64_response.content

    if not CONFIG["API"]["PICGO_KEY"] and not CONFIG["API"]["TUMY_KEY"]:
        base64_image = base64.b64encode(image_buffer).decode('utf-8')
        image_content_type = image_base64_response.headers.get('content-type', 'image/jpeg')
        return f"![image](data:{image_content_type};base64,{base64_image})"

    logger.info("开始上传图床", "Server")

    if CONFIG["API"]["PICGO_KEY"]:
        files = {'source': ('image.jpg', image_buffer, 'image/jpeg')}
        headers = {
            "X-API-Key": CONFIG["API"]["PICGO_KEY"]
        }

        proxy_options = Utils.get_proxy_options_for_requests()
        response_url = requests.post(
            "https://www.picgo.net/api/1/upload",
            files=files,
            headers=headers,
            **proxy_options
        )

        if response_url.status_code != 200:
            return "生图失败，请查看PICGO图床密钥是否设置正确"
        else:
            logger.info("生图成功", "Server")
            result = response_url.json()
            return f"![image]({result['image']['url']})"


    elif CONFIG["API"]["TUMY_KEY"]:
        files = {'file': ('image.jpg', image_buffer, 'image/jpeg')}
        headers = {
            "Accept": "application/json",
            'Authorization': f"Bearer {CONFIG['API']['TUMY_KEY']}"
        }

        proxy_options = Utils.get_proxy_options_for_requests()
        response_url = requests.post(
            "https://tu.my/api/v1/upload",
            files=files,
            headers=headers,
            **proxy_options
        )

        if response_url.status_code != 200:
            return "生图失败，请查看TUMY图床密钥是否设置正确"
        else:
            try:
                result = response_url.json()
                logger.info("生图成功", "Server")
                return f"![image]({result['data']['links']['url']})"
            except Exception as error:
                logger.error(str(error), "Server")
                return "生图失败，请查看TUMY图床密钥是否设置正确"

def handle_non_stream_response(response, model):
    try:
        logger.info("开始处理非流式响应", "Server")

        stream = response.iter_lines()
        full_response = ""

        CONFIG["IS_THINKING"] = False
        CONFIG["IS_IMG_GEN"] = False
        CONFIG["IS_IMG_GEN2"] = False

        for chunk in stream:
            if not chunk:
                continue
            try:
                line_json = json.loads(chunk.decode("utf-8").strip())
                if line_json.get("error"):
                    logger.error(json.dumps(line_json, indent=2), "Server")
                    return json.dumps({"error": "RateLimitError"}) + "\n\n"

                response_data = line_json.get("result", {}).get("response")
                if not response_data:
                    continue

                if response_data.get("doImgGen") or response_data.get("imageAttachmentInfo"):
                    CONFIG["IS_IMG_GEN"] = True

                result = process_model_response(response_data, model)

                if result["token"]:
                    full_response += result["token"]

                if result["imageUrl"]:
                    CONFIG["IS_IMG_GEN2"] = True
                    return handle_image_response(result["imageUrl"])

            except json.JSONDecodeError:
                continue
            except Exception as e:
                logger.error(f"处理流式响应行时出错: {str(e)}", "Server")
                continue

        return full_response
    except Exception as error:
        logger.error(str(error), "Server")
        raise
def handle_stream_response(response, model):
    def generate():
        logger.info("开始处理流式响应", "Server")

        stream = response.iter_lines()
        CONFIG["IS_THINKING"] = False
        CONFIG["IS_IMG_GEN"] = False
        CONFIG["IS_IMG_GEN2"] = False

        try:
            for chunk in stream:
                if not chunk:
                    continue
                try:
                    line_json = json.loads(chunk.decode("utf-8").strip())
                    print(line_json)
                    if line_json.get("error"):
                        logger.error(json.dumps(line_json, indent=2), "Server")
                        yield json.dumps({"error": "RateLimitError"}) + "\n\n"
                        return

                    response_data = line_json.get("result", {}).get("response")
                    if not response_data:
                        continue

                    if response_data.get("doImgGen") or response_data.get("imageAttachmentInfo"):
                        CONFIG["IS_IMG_GEN"] = True

                    result = process_model_response(response_data, model)

                    if result["token"]:
                        yield f"data: {json.dumps(MessageProcessor.create_chat_response(result['token'], model, True))}\n\n"

                    if result["imageUrl"]:
                        CONFIG["IS_IMG_GEN2"] = True
                        image_data = handle_image_response(result["imageUrl"])
                        yield f"data: {json.dumps(MessageProcessor.create_chat_response(image_data, model, True))}\n\n"

                except json.JSONDecodeError:
                    continue
                except Exception as e:
                    logger.error(f"处理流式响应行时出错: {str(e)}", "Server")
                    continue

        except Exception as stream_error:
            logger.error(f"流式响应读取失败: {str(stream_error)}", "Server")
            yield f"data: {json.dumps(MessageProcessor.create_chat_response('网络连接中断，请重试', model, True))}\n\n"

        yield "data: [DONE]\n\n"
    return generate()

def initialization():
    # 初始化代理池
    Utils.init_proxy_pool()
    
    sso_array = os.environ.get("SSO", "").split(',')
    sso_pro_array = os.environ.get("SSO_PRO", "").split(',')
    logger.info("开始加载令牌", "Server")
    token_manager.load_token_status()
    for sso in sso_array:
        if sso:
            token_manager.add_token(f"sso-rw={sso};sso={sso}",True)
    
    # 加载SSO_PRO令牌（仅用于grok-4）
    logger.info("开始加载SSO_PRO令牌", "Server")
    for sso_pro in sso_pro_array:
        if sso_pro:
            token_manager.add_pro_token(f"sso-rw={sso_pro};sso={sso_pro}",True)
    token_manager.save_token_status()

    logger.info(f"成功加载令牌: {json.dumps(token_manager.get_all_tokens(), indent=2)}", "Server")
    logger.info(f"令牌加载完成，共加载: {len(token_manager.get_all_tokens())}个令牌", "Server")

logger.info("初始化完成", "Server")


app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app)
app.secret_key = os.environ.get('FLASK_SECRET_KEY') or secrets.token_hex(16)
app.json.sort_keys = False

@app.route('/manager/login', methods=['GET', 'POST'])
def manager_login():
    if CONFIG["ADMIN"]["MANAGER_SWITCH"]:
        if request.method == 'POST':
            password = request.form.get('password')
            if password == CONFIG["ADMIN"]["PASSWORD"]:
                session['is_logged_in'] = True
                return redirect('/manager')
            return render_template('login.html', error=True)
        return render_template('login.html', error=False)
    else:
        return redirect('/')

def check_auth():
    return session.get('is_logged_in', False)

@app.route('/manager')
def manager():
    if not check_auth():
        return redirect('/manager/login')
    return render_template('manager.html')

@app.route('/manager/api/get')
def get_manager_tokens():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401
    
    # 获取基本令牌状态
    token_status = token_manager.get_token_status_map()
    
    # 添加每日使用情况信息
    today = token_manager.get_today_key()
    daily_usage = token_manager.free_grok4_usage.get(today, {})
    today_usage = sum(daily_usage.values())
    
    # 计算每日限制：令牌数量 × 每个令牌10次
    grok4_free_token_count = len(token_manager.token_model_map.get("grok-4-free", []))
    daily_limit = grok4_free_token_count * 10
    
    result = {
        "tokens": token_status,
        "dailyUsage": {
            "today": today,
            "grok4Free": {
                "used": today_usage,
                "limit": daily_limit,
                "remaining": max(0, daily_limit - today_usage),
                "tokenCount": grok4_free_token_count
            }
        }
    }
    
    return jsonify(result)

@app.route('/manager/api/add', methods=['POST'])
def add_manager_token():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401
    try:
        sso = request.json.get('sso')
        if not sso:
            return jsonify({"error": "SSO token is required"}), 400
        token_manager.add_token(f"sso-rw={sso};sso={sso}")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/manager/api/delete', methods=['POST'])
def delete_manager_token():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401
    try:
        sso = request.json.get('sso')
        if not sso:
            return jsonify({"error": "SSO token is required"}), 400
        token_manager.delete_token(f"sso-rw={sso};sso={sso}")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
@app.route('/manager/api/cf_clearance', methods=['POST'])   
def setCf_Manager_clearance():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401
    try:
        cf_clearance = request.json.get('cf_clearance')
        if not cf_clearance:
            return jsonify({"error": "cf_clearance is required"}), 400
        CONFIG["SERVER"]['CF_CLEARANCE'] = cf_clearance
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/get/tokens', methods=['GET'])
def get_tokens():
    auth_token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if CONFIG["API"]["IS_CUSTOM_SSO"]:
        return jsonify({"error": '自定义的SSO令牌模式无法获取轮询sso令牌状态'}), 403
    elif auth_token != CONFIG["API"]["API_KEY"]:
        return jsonify({"error": 'Unauthorized'}), 401
    return jsonify(token_manager.get_token_status_map())

@app.route('/add/token', methods=['POST'])
def add_token():
    auth_token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if CONFIG["API"]["IS_CUSTOM_SSO"]:
        return jsonify({"error": '自定义的SSO令牌模式无法添加sso令牌'}), 403
    elif auth_token != CONFIG["API"]["API_KEY"]:
        return jsonify({"error": 'Unauthorized'}), 401

    try:
        sso = request.json.get('sso')
        token_manager.add_token(f"sso-rw={sso};sso={sso}")
        return jsonify(token_manager.get_token_status_map().get(sso, {})), 200
    except Exception as error:
        logger.error(str(error), "Server")
        return jsonify({"error": '添加sso令牌失败'}), 500
    
@app.route('/set/cf_clearance', methods=['POST'])
def setCf_clearance():
    auth_token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if auth_token != CONFIG["API"]["API_KEY"]:
        return jsonify({"error": 'Unauthorized'}), 401
    try:
        cf_clearance = request.json.get('cf_clearance')
        CONFIG["SERVER"]['CF_CLEARANCE'] = cf_clearance
        return jsonify({"message": '设置cf_clearance成功'}), 200
    except Exception as error:
        logger.error(str(error), "Server")
        return jsonify({"error": '设置cf_clearance失败'}), 500
    
@app.route('/delete/token', methods=['POST'])
def delete_token():
    auth_token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if CONFIG["API"]["IS_CUSTOM_SSO"]:
        return jsonify({"error": '自定义的SSO令牌模式无法删除sso令牌'}), 403
    elif auth_token != CONFIG["API"]["API_KEY"]:
        return jsonify({"error": 'Unauthorized'}), 401

    try:
        sso = request.json.get('sso')
        token_manager.delete_token(f"sso-rw={sso};sso={sso}")
        return jsonify({"message": '删除sso令牌成功'}), 200
    except Exception as error:
        logger.error(str(error), "Server")
        return jsonify({"error": '删除sso令牌失败'}), 500

@app.route('/v1/models', methods=['GET'])
def get_models():
    return jsonify({
        "object": "list",
        "data": [
            {
                "id": model,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "grok"
            }
            for model in CONFIG["MODELS"].keys()
        ]
    })

@app.route('/v1/chat/completions', methods=['POST'])
def chat_completions():
    response_status_code = 500
    try:
        auth_token = request.headers.get('Authorization',
                                         '').replace('Bearer ', '')
        if auth_token:
            if CONFIG["API"]["IS_CUSTOM_SSO"]:
                result = f"sso={auth_token};sso-rw={auth_token}"
                token_manager.set_token(result)
            elif auth_token != CONFIG["API"]["API_KEY"]:
                return jsonify({"error": 'Unauthorized'}), 401
        else:
            return jsonify({"error": 'API_KEY缺失'}), 401

        data = request.json
        model = data.get("model")
        stream = data.get("stream", False)
        
        # 如果用户请求 grok-4，自动选择合适的实现
        if model == "grok-4":
            # 优先使用 SSO_PRO 令牌的 grok-4
            if token_manager.get_token_count_for_model("grok-4") > 0:
                model = "grok-4"
            # 如果没有 SSO_PRO 令牌，使用普通令牌的 grok-4-free  
            elif token_manager.get_token_count_for_model("grok-4-free") > 0:
                model = "grok-4-free"
                logger.info("使用普通账号的grok-4-free服务", "Server")
            else:
                return jsonify({
                    "error": {
                        "message": "grok-4 模型暂无可用令牌，请稍后重试",
                        "type": "server_error"
                    }
                }), 429

        retry_count = 0
        is_network_error_retry = False
        grok_client = GrokApiClient(model)
        request_payload = grok_client.prepare_chat_request(data)
        logger.info(json.dumps(request_payload,indent=2))

        while retry_count < CONFIG["RETRY"]["MAX_ATTEMPTS"]:
            retry_count += 1
            
            # 如果是网络错误重试，先恢复之前减少的计数，然后获取当前令牌
            if is_network_error_retry:
                # 重置标记
                is_network_error_retry = False
                # 获取当前令牌而不增加计数
                CONFIG["API"]["SIGNATURE_COOKIE"] = Utils.create_auth_headers(model, True)
            else:
                # 正常获取下一个令牌并增加计数
                CONFIG["API"]["SIGNATURE_COOKIE"] = Utils.create_auth_headers(model)

            if not CONFIG["API"]["SIGNATURE_COOKIE"]:
                raise ValueError('该模型无可用令牌')

            logger.info(
                f"当前令牌: {json.dumps(CONFIG['API']['SIGNATURE_COOKIE'], indent=2)}","Server")
            logger.info(
                f"当前可用模型的全部可用数量: {json.dumps(token_manager.get_remaining_token_request_capacity(), indent=2)}","Server")
            
            if CONFIG['SERVER']['CF_CLEARANCE']:
                CONFIG["SERVER"]['COOKIE'] = f"{CONFIG['API']['SIGNATURE_COOKIE']};{CONFIG['SERVER']['CF_CLEARANCE']}" 
            else:
                CONFIG["SERVER"]['COOKIE'] = CONFIG['API']['SIGNATURE_COOKIE']
            logger.info(json.dumps(request_payload,indent=2),"Server")
            try:
                # 添加请求间延迟，避免被检测
                time.sleep(1)
                
                # 生成必要的请求头
                xai_request_id = Utils.generate_xai_request_id()
                statsig_id = Utils.get_statsig_id()
                
                # 构建请求头
                request_headers = {
                    **DEFAULT_HEADERS, 
                    "Cookie": CONFIG["SERVER"]['COOKIE'],
                    "x-xai-request-id": xai_request_id
                }
                
                # 如果成功获取到 statsig_id 则添加到请求头
                if statsig_id:
                    request_headers["x-statsig-id"] = statsig_id
                    logger.info(f"添加 x-statsig-id 到请求头", "Server")
                else:
                    logger.warning("无法获取 x-statsig-id，尝试不带签名发送请求", "Server")
                
                proxy_options = Utils.get_proxy_options()
                response = curl_requests.post(
                    f"{CONFIG['API']['BASE_URL']}/rest/app-chat/conversations/new",
                    headers=request_headers,
                    data=json.dumps(request_payload),
                    impersonate="chrome133a",
                    stream=True,
                    timeout=30,
                    verify=True,
                    **proxy_options)
                logger.info(CONFIG["SERVER"]['COOKIE'],"Server")
                if response.status_code == 200:
                    response_status_code = 200
                    logger.info("请求成功", "Server")
                    logger.info(f"当前{model}剩余可用令牌数: {token_manager.get_token_count_for_model(model)}","Server")

                    try:
                        if stream:
                            return Response(stream_with_context(
                                handle_stream_response(response, model)),content_type='text/event-stream')
                        else:
                            content = handle_non_stream_response(response, model)
                            return jsonify(
                                MessageProcessor.create_chat_response(content, model))

                    except Exception as error:
                        logger.error(str(error), "Server")
                        if CONFIG["API"]["IS_CUSTOM_SSO"]:
                            raise ValueError(f"自定义SSO令牌当前模型{model}的请求次数已失效")
                        
                        # 如果是网络连接错误，减少请求计数但不移除令牌
                        if Utils.is_network_error(error):
                            logger.info(f"响应处理时检测到网络连接错误，减少请求计数但保留令牌: {str(error)}", "Server")
                            token_manager.reduce_token_request_count(model, 1)
                            is_network_error_retry = True  # 标记为网络错误重试
                            # 网络错误时继续重试，不抛出异常
                            if token_manager.get_token_count_for_model(model) == 0:
                                raise ValueError(f"{model} 次数已达上限，请切换其他模型或者重新对话")
                            continue  # 继续重试循环
                        else:
                            # 其他错误则移除令牌
                            logger.info(f"响应处理时检测到非网络错误，移除令牌: {str(error)}", "Server")
                            token_manager.remove_token_for_model(model, CONFIG["API"]["SIGNATURE_COOKIE"])
                            if token_manager.get_token_count_for_model(model) == 0:
                                raise ValueError(f"{model} 次数已达上限，请切换其他模型或者重新对话")
                elif response.status_code == 403:
                    response_status_code = 403
                    token_manager.reduce_token_request_count(model,1)#重置去除当前因为错误未成功请求的次数，确保不会因为错误未成功请求的次数导致次数上限
                    if token_manager.get_token_count_for_model(model) == 0:
                        raise ValueError(f"{model} 次数已达上限，请切换其他模型或者重新对话")
                    print("状态码:", response.status_code)
                    print("响应头:", response.headers)
                    print("响应内容:", response.text)
                    raise ValueError(f"IP暂时被封无法破盾，请稍后重试或者更换ip")
                elif response.status_code == 429:
                    response_status_code = 429
                    token_manager.reduce_token_request_count(model,1)
                    if CONFIG["API"]["IS_CUSTOM_SSO"]:
                        raise ValueError(f"自定义SSO令牌当前模型{model}的请求次数已失效")

                    token_manager.remove_token_for_model(
                        model, CONFIG["API"]["SIGNATURE_COOKIE"])
                    if token_manager.get_token_count_for_model(model) == 0:
                        raise ValueError(f"{model} 次数已达上限，请切换其他模型或者重新对话")

                else:
                    if CONFIG["API"]["IS_CUSTOM_SSO"]:
                        raise ValueError(f"自定义SSO令牌当前模型{model}的请求次数已失效")

                    logger.error(f"令牌异常错误状态!status: {response.status_code}","Server")
                    token_manager.remove_token_for_model(model, CONFIG["API"]["SIGNATURE_COOKIE"])
                    logger.info(
                        f"当前{model}剩余可用令牌数: {token_manager.get_token_count_for_model(model)}",
                        "Server")

            except Exception as e:
                logger.error(f"请求处理异常: {str(e)}", "Server")
                if CONFIG["API"]["IS_CUSTOM_SSO"]:
                    raise
                
                # 如果是网络连接错误，减少请求计数但不移除令牌
                if Utils.is_network_error(e):
                    logger.info(f"检测到网络连接错误，减少请求计数但保留令牌: {str(e)}", "Server")
                    token_manager.reduce_token_request_count(model, 1)
                    is_network_error_retry = True  # 标记为网络错误重试
                else:
                    # 其他错误则移除令牌
                    logger.info(f"检测到非网络错误，移除令牌: {str(e)}", "Server")
                    token_manager.remove_token_for_model(model, CONFIG["API"]["SIGNATURE_COOKIE"])
                
                # 检查是否还有可用令牌
                if token_manager.get_token_count_for_model(model) == 0:
                    raise ValueError(f"{model} 次数已达上限，请切换其他模型或者重新对话")
                
                continue
        if response_status_code == 403:
            raise ValueError('IP暂时被封无法破盾，请稍后重试或者更换ip')
        elif response_status_code == 500:
            raise ValueError('当前模型所有令牌暂无可用，请稍后重试')
        else:
            raise ValueError('请求失败，请检查网络连接或稍后重试')

    except Exception as error:
        logger.error(str(error), "ChatAPI")
        return jsonify(
            {"error": {
                "message": str(error),
                "type": "server_error"
            }}), response_status_code

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def catch_all(path):
    return 'api运行正常', 200

if __name__ == '__main__':
    token_manager = AuthTokenManager()
    initialization()

    app.run(
        host='0.0.0.0',
        port=CONFIG["SERVER"]["PORT"],
        debug=False
    )
