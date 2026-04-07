#!/usr/bin/env python3
"""
Outlook邮件批量获取工具

批量获取多个邮箱账户的邮件列表，并保存为JSON格式
基于IMAP协议和OAuth2认证

Author: Outlook Manager Team
Version: 1.0.0
"""

import asyncio
import email
import imaplib
import json
import logging
import re
import socket
import time
from datetime import datetime
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path
from queue import Empty, Queue
from typing import Dict, List, Optional
import httpx
import os

# ============================================================================
# 配置常量
# ============================================================================

# 文件路径配置
ACCOUNTS_FILE = "accounts.json"
OUTPUT_DIR = "email_lists"
OUTPUT_FILE_FORMAT = "{email_id}_{date}.json"

# OAuth2配置
TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
OAUTH_SCOPE = "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"

# IMAP服务器配置
IMAP_SERVER = "outlook.live.com"
IMAP_PORT = 993

# 连接池配置
MAX_CONNECTIONS = 5
CONNECTION_TIMEOUT = 30
SOCKET_TIMEOUT = 15

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ============================================================================
# 数据模型
# ============================================================================

class AccountCredentials:
    """账户凭证模型"""
    def __init__(self, email: str, refresh_token: str, client_id: str, tags: Optional[List[str]] = None):
        self.email = email
        self.refresh_token = refresh_token
        self.client_id = client_id
        self.tags = tags or []


class EmailItem:
    """邮件项目模型"""
    def __init__(self, message_id: str, folder: str, subject: str, from_email: str, date: str, 
                 is_read: bool = False, has_attachments: bool = False, sender_initial: str = "?"):
        self.message_id = message_id
        self.folder = folder
        self.subject = subject
        self.from_email = from_email
        self.date = date
        self.is_read = is_read
        self.has_attachments = has_attachments
        self.sender_initial = sender_initial
    
    def to_dict(self):
        """转换为字典格式"""
        return {
            "message_id": self.message_id,
            "folder": self.folder,
            "subject": self.subject,
            "from_email": self.from_email,
            "date": self.date,
            "is_read": self.is_read,
            "has_attachments": self.has_attachments,
            "sender_initial": self.sender_initial
        }


# ============================================================================
# IMAP连接池管理
# ============================================================================

class IMAPConnectionPool:
    """
    IMAP连接池管理器

    提供连接复用、自动重连、连接状态监控等功能
    优化IMAP连接性能，减少连接建立开销
    """

    def __init__(self, max_connections: int = MAX_CONNECTIONS):
        """
        初始化连接池

        Args:
            max_connections: 每个邮箱的最大连接数
        """
        self.max_connections = max_connections
        self.connections = {}  # {email: Queue of connections}
        self.connection_count = {}  # {email: active connection count}
        self.lock = asyncio.Lock()
        logger.info(f"Initialized IMAP connection pool with max_connections={max_connections}")

    async def _create_connection(self, email: str, access_token: str) -> imaplib.IMAP4_SSL:
        """
        创建新的IMAP连接

        Args:
            email: 邮箱地址
            access_token: OAuth2访问令牌

        Returns:
            IMAP4_SSL: 已认证的IMAP连接

        Raises:
            Exception: 连接创建失败
        """
        try:
            # 设置全局socket超时
            socket.setdefaulttimeout(SOCKET_TIMEOUT)

            # 创建SSL IMAP连接
            imap_client = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)

            # 设置连接超时
            imap_client.sock.settimeout(CONNECTION_TIMEOUT)

            # XOAUTH2认证
            auth_string = f"user={email}\x01auth=Bearer {access_token}\x01\x01".encode('utf-8')
            imap_client.authenticate('XOAUTH2', lambda _: auth_string)

            logger.info(f"Successfully created IMAP connection for {email}")
            return imap_client

        except Exception as e:
            logger.error(f"Failed to create IMAP connection for {email}: {e}")
            raise

    async def get_connection(self, email: str, access_token: str) -> imaplib.IMAP4_SSL:
        """
        获取IMAP连接（从池中复用或创建新连接）

        Args:
            email: 邮箱地址
            access_token: OAuth2访问令牌

        Returns:
            IMAP4_SSL: 可用的IMAP连接

        Raises:
            Exception: 无法获取连接
        """
        async with self.lock:
            # 初始化邮箱的连接池
            if email not in self.connections:
                self.connections[email] = Queue(maxsize=self.max_connections)
                self.connection_count[email] = 0

            connection_queue = self.connections[email]

            # 尝试从池中获取现有连接
            try:
                connection = connection_queue.get_nowait()
                # 测试连接有效性
                try:
                    connection.noop()
                    logger.debug(f"Reused existing IMAP connection for {email}")
                    return connection
                except Exception:
                    # 连接已失效，需要创建新连接
                    logger.debug(f"Existing connection invalid for {email}, creating new one")
                    self.connection_count[email] -= 1
            except Empty:
                # 池中没有可用连接
                pass

            # 检查是否可以创建新连接
            if self.connection_count[email] < self.max_connections:
                connection = await self._create_connection(email, access_token)
                self.connection_count[email] += 1
                return connection
            else:
                # 达到最大连接数，等待可用连接
                logger.warning(f"Max connections ({self.max_connections}) reached for {email}, waiting...")
                try:
                    return connection_queue.get(timeout=30)
                except Exception as e:
                    logger.error(f"Timeout waiting for connection for {email}: {e}")
                    raise

    async def return_connection(self, email: str, connection: imaplib.IMAP4_SSL) -> None:
        """
        归还连接到池中

        Args:
            email: 邮箱地址
            connection: 要归还的IMAP连接
        """
        if email not in self.connections:
            logger.warning(f"Attempting to return connection for unknown email: {email}")
            return

        try:
            # 测试连接状态
            connection.noop()
            # 连接有效，归还到池中
            self.connections[email].put_nowait(connection)
            logger.debug(f"Successfully returned IMAP connection for {email}")
        except Exception as e:
            # 连接已失效，减少计数并丢弃
            async with self.lock:
                if email in self.connection_count:
                    self.connection_count[email] = max(0, self.connection_count[email] - 1)
            logger.debug(f"Discarded invalid connection for {email}: {e}")

    async def close_all_connections(self, email: str = None) -> None:
        """
        关闭所有连接

        Args:
            email: 指定邮箱地址，如果为None则关闭所有邮箱的连接
        """
        async with self.lock:
            if email:
                # 关闭指定邮箱的所有连接
                if email in self.connections:
                    closed_count = 0
                    while not self.connections[email].empty():
                        try:
                            conn = self.connections[email].get_nowait()
                            conn.logout()
                            closed_count += 1
                        except Exception as e:
                            logger.debug(f"Error closing connection: {e}")

                    self.connection_count[email] = 0
                    logger.info(f"Closed {closed_count} connections for {email}")
            else:
                # 关闭所有邮箱的连接
                total_closed = 0
                for email_key in list(self.connections.keys()):
                    count_before = self.connection_count.get(email_key, 0)
                    await self.close_all_connections(email_key)
                    total_closed += count_before
                logger.info(f"Closed total {total_closed} connections for all accounts")


# ============================================================================
# 辅助函数
# ============================================================================

def decode_header_value(header_value: str) -> str:
    """
    解码邮件头字段

    处理各种编码格式的邮件头部信息，如Subject、From等

    Args:
        header_value: 原始头部值

    Returns:
        str: 解码后的字符串
    """
    if not header_value:
        return ""

    try:
        decoded_parts = decode_header(str(header_value))
        decoded_string = ""

        for part, charset in decoded_parts:
            if isinstance(part, bytes):
                try:
                    # 使用指定编码或默认UTF-8解码
                    encoding = charset if charset else 'utf-8'
                    decoded_string += part.decode(encoding, errors='replace')
                except (LookupError, UnicodeDecodeError):
                    # 编码失败时使用UTF-8强制解码
                    decoded_string += part.decode('utf-8', errors='replace')
            else:
                decoded_string += str(part)

        return decoded_string.strip()
    except Exception as e:
        logger.warning(f"Failed to decode header value '{header_value}': {e}")
        return str(header_value) if header_value else ""


# ============================================================================
# 账户凭证管理模块
# ============================================================================

async def get_account_credentials() -> Dict[str, AccountCredentials]:
    """
    从accounts.json文件获取所有邮箱的账户凭证

    Returns:
        Dict[str, AccountCredentials]: 邮箱地址到账户凭证的映射

    Raises:
        Exception: 文件读取失败
    """
    try:
        # 检查账户文件是否存在
        accounts_path = Path(ACCOUNTS_FILE)
        if not accounts_path.exists():
            logger.error(f"Accounts file {ACCOUNTS_FILE} not found")
            raise FileNotFoundError(f"Accounts file {ACCOUNTS_FILE} not found")

        # 读取账户数据
        with open(accounts_path, 'r', encoding='utf-8') as f:
            accounts_data = json.load(f)

        # 转换为AccountCredentials对象
        credentials = {}
        for email_id, account_info in accounts_data.items():
            # 验证账户数据完整性
            required_fields = ['refresh_token', 'client_id']
            missing_fields = [field for field in required_fields if not account_info.get(field)]

            if missing_fields:
                logger.warning(f"Account {email_id} missing required fields: {missing_fields}")
                continue

            credentials[email_id] = AccountCredentials(
                email=email_id,
                refresh_token=account_info['refresh_token'],
                client_id=account_info['client_id'],
                tags=account_info.get('tags', [])
            )

        logger.info(f"Loaded {len(credentials)} account(s) from {ACCOUNTS_FILE}")
        return credentials

    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in accounts file: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error getting account credentials: {e}")
        raise


# ============================================================================
# OAuth2令牌管理模块
# ============================================================================

async def get_access_token(credentials: AccountCredentials) -> str:
    """
    使用refresh_token获取access_token

    Args:
        credentials: 账户凭证信息

    Returns:
        str: OAuth2访问令牌

    Raises:
        Exception: 令牌获取失败
    """
    # 构建OAuth2请求数据
    token_request_data = {
        'client_id': credentials.client_id,
        'grant_type': 'refresh_token',
        'refresh_token': credentials.refresh_token,
        'scope': OAUTH_SCOPE
    }

    try:
        # 发送令牌请求，明确禁用代理
        async with httpx.AsyncClient(timeout=30.0, proxies=None) as client:
            response = await client.post(TOKEN_URL, data=token_request_data)
            response.raise_for_status()

            # 解析响应
            token_data = response.json()
            access_token = token_data.get('access_token')

            if not access_token:
                logger.error(f"No access token in response for {credentials.email}")
                raise ValueError("Failed to obtain access token from response")

            new_refresh_token = token_data.get("refresh_token")
            if new_refresh_token and new_refresh_token != credentials.refresh_token:
                credentials.refresh_token = new_refresh_token
                # Need to implement saving it back if batch.py supports it, but batch.py might not have save_account_credentials.
                # Actually let's just log it or save it via another way if possible.
                logger.info(f"Updated refresh token for {credentials.email}")

            logger.info(f"Successfully obtained access token for {credentials.email}")
            return access_token

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP {e.response.status_code} error getting access token for {credentials.email}: {e}")
        raise
    except httpx.RequestError as e:
        logger.error(f"Request error getting access token for {credentials.email}: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error getting access token for {credentials.email}: {e}")
        raise


# ============================================================================
# IMAP核心服务 - 邮件列表
# ============================================================================

async def list_emails(imap_pool: IMAPConnectionPool, credentials: AccountCredentials) -> List[Dict]:
    """
    获取邮件列表

    Args:
        imap_pool: IMAP连接池
        credentials: 账户凭证

    Returns:
        List[Dict]: 邮件列表，每个邮件包含基本信息
    """
    access_token = await get_access_token(credentials)
    email_items = []

    # 获取IMAP连接
    imap_client = await imap_pool.get_connection(credentials.email, access_token)
    
    try:
        # 检查要获取的文件夹
        folders_to_check = ["INBOX", "Junk"]
        
        for folder_name in folders_to_check:
            try:
                # 选择文件夹
                imap_client.select(f'"{folder_name}"', readonly=True)
                
                # 搜索所有邮件
                status, messages = imap_client.search(None, "ALL")
                if status != 'OK' or not messages or not messages[0]:
                    logger.warning(f"No messages found in {folder_name} for {credentials.email}")
                    continue
                    
                message_ids = messages[0].split()
                
                # 通常ID越大越新，所以反转列表
                message_ids.reverse()
                
                # 批量获取邮件头
                for i in range(0, len(message_ids), 100):  # 每次处理100封邮件
                    batch_ids = message_ids[i:i+100]
                    msg_id_sequence = b','.join(batch_ids)
                    
                    # 只获取必要的头部信息
                    status, msg_data = imap_client.fetch(msg_id_sequence, '(FLAGS BODY.PEEK[HEADER.FIELDS (SUBJECT DATE FROM MESSAGE-ID)])')

                    if status != 'OK':
                        logger.warning(f"Failed to fetch emails from {folder_name} for {credentials.email}")
                        continue
                    
                    # 解析批量获取的数据
                    for j in range(0, len(msg_data), 2):
                        if j+1 >= len(msg_data):
                            continue
                            
                        header_data = msg_data[j][1]
                        
                        # 从返回的原始数据中解析出msg_id
                        match = re.match(rb'(\d+)\s+\(', msg_data[j][0])
                        if not match:
                            continue
                        fetched_msg_id = match.group(1)

                        msg = email.message_from_bytes(header_data)
                        
                        subject = decode_header_value(msg.get('Subject', '(No Subject)'))
                        from_email = decode_header_value(msg.get('From', '(Unknown Sender)'))
                        date_str = msg.get('Date', '')
                        
                        try:
                            date_obj = parsedate_to_datetime(date_str) if date_str else datetime.now()
                            formatted_date = date_obj.isoformat()
                        except:
                            date_obj = datetime.now()
                            formatted_date = date_obj.isoformat()
                        
                        message_id = f"{folder_name}-{fetched_msg_id.decode()}"
                        
                        # 提取发件人首字母
                        sender_initial = "?"
                        if from_email:
                            # 尝试提取邮箱用户名的首字母
                            email_match = re.search(r'([a-zA-Z])', from_email)
                            if email_match:
                                sender_initial = email_match.group(1).upper()
                        
                        # 检查是否已读
                        is_read = b'\\Seen' in msg_data[j][0]
                        
                        email_item = {
                            "email_id": credentials.email,
                            "message_id": message_id,
                            "folder": folder_name,
                            "subject": subject,
                            "from_email": from_email,
                            "date": formatted_date,
                            "is_read": is_read,
                            "sender_initial": sender_initial
                        }
                        email_items.append(email_item)

            except Exception as e:
                logger.warning(f"Failed to fetch emails from {folder_name} for {credentials.email}: {e}")
                continue

        # 按日期重新排序最终结果
        email_items.sort(key=lambda x: x["date"], reverse=True)
        
        # 归还连接到池中
        await imap_pool.return_connection(credentials.email, imap_client)
        
        logger.info(f"Retrieved {len(email_items)} emails for {credentials.email}")
        return email_items

    except Exception as e:
        logger.error(f"Error listing emails for {credentials.email}: {e}")
        try:
            # 如果出错，尝试归还连接
            if hasattr(imap_client, 'state') and imap_client.state != 'LOGOUT':
                await imap_pool.return_connection(credentials.email, imap_client)
        except:
            pass
        raise


# ============================================================================
# 主函数
# ============================================================================

async def main():
    """主函数"""
    try:
        # 创建输出目录
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        
        # 获取所有账户凭证
        accounts = await get_account_credentials()
        if not accounts:
            logger.error("No valid accounts found")
            return
            
        logger.info(f"Processing {len(accounts)} accounts")
        
        # 创建IMAP连接池
        imap_pool = IMAPConnectionPool()
        
        # 获取当前日期作为文件名的一部分
        current_date = datetime.now().strftime("%Y%m%d")
        
        # 处理每个账户
        for email_id, credentials in accounts.items():
            try:
                logger.info(f"Processing account: {email_id}")
                
                # 获取邮件列表
                emails = await list_emails(imap_pool, credentials)
                
                # 保存为JSON文件
                output_file = os.path.join(OUTPUT_DIR, OUTPUT_FILE_FORMAT.format(
                    email_id=email_id.replace("@", "_at_"),
                    date=current_date
                ))
                
                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(emails, f, indent=2, ensure_ascii=False)
                    
                logger.info(f"Saved {len(emails)} emails to {output_file}")
                
            except Exception as e:
                logger.error(f"Failed to process account {email_id}: {e}")
                continue
                
        # 关闭所有连接
        await imap_pool.close_all_connections()
        
    except Exception as e:
        logger.error(f"Error in main function: {e}")


if __name__ == "__main__":
    logger.info("Starting batch email retrieval")
    asyncio.run(main())
    logger.info("Batch email retrieval completed")