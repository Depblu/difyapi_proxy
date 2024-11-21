import json
import requests
import os
from enum import Enum


class DifyAPIError(Exception):
    """自定义异常类用于处理 Dify API 错误"""
    pass


class ResponseMode(Enum):
    STREAMING = "streaming"  # 流式模式（推荐）。基于 SSE（Server-Sent Events）实现类似打字机输出方式的流式返回。
    BLOCKING = "blocking"    # 阻塞模式，等待执行完毕后返回结果。（请求若流程较长可能会被中断）。由于 Cloudflare 限制，请求会在 100 秒超时无返回后中断。
    

def dify_api_error_handler(response):
    """处理API错误响应"""
    if response.status_code != 200:
        try:
            error_info = response.json()
            error_code = error_info.get('code', 'unknown_error')
            error_message = error_info.get('message', '未知错误')
        except ValueError:
            error_code = 'unknown_error'
            error_message = response.text or '未知错误'
        raise DifyAPIError(f"发送聊天消息失败: {error_code} - {error_message}")


def create_headers(api_key: str, include_content_type: bool = False) -> dict:
    """
    创建请求头
    
    参数:
        api_key (str): API密钥
        include_content_type (bool): 是否包含Content-Type头
    """
    headers = {
        'Authorization': f'Bearer {api_key}'
    }
    if include_content_type:
        headers['Content-Type'] = 'application/json'
    return headers


def upload_files(api_base_url: str, headers: dict, files: list[str], user: str) -> list[str]:
    """
    上传文件到 Dify API 并返回文件 ID 列表

    参数:
        api_base_url (str): API 基础 URL
        headers (dict): 包含认证信息的请求头
        files (list[str]): 要上传的文件路径列表
        user (str): 用户标识

    返回:
        list[str]: 上传成功的文件 ID 列表

    抛出:
        DifyAPIError: 当文件不存在、格式不支持或上传失败时
    """
    uploaded_file_ids = []
    
    if not files:
        return uploaded_file_ids
        
    upload_url = f'{api_base_url}/files/upload'
    for file_path in files:
        if not os.path.isfile(file_path):
            raise DifyAPIError(f"文件不存在: {file_path}")

        file_name, file_ext = os.path.splitext(os.path.basename(file_path))
        file_ext = file_ext.lower().lstrip('.')
        if file_ext not in ['png', 'jpg', 'jpeg', 'webp', 'gif']:
            raise DifyAPIError(f"不支持的文件类型: {file_ext} (文件: {file_path})")

        with open(file_path, 'rb') as f:
            files_payload = {
                'file': (os.path.basename(file_path), f, f'image/{file_ext}')
            }
            data = {
                'user': user
            }
            response = requests.post(upload_url, headers=headers, files=files_payload, data=data)

        if response.status_code != 200 and response.status_code != 201:
            try:
                error_info = response.json()
                error_message = error_info.get('message', '未知错误')
            except ValueError:
                error_message = response.text or '未知错误'
            raise DifyAPIError(f"文件上传失败 ({file_path}): {error_message}")

        upload_response = response.json()
        uploaded_file_ids.append(upload_response['id'])
    
    return uploaded_file_ids


def prepare_chat_request(api_key: str, query: str, response_mode: ResponseMode, user: str, 
                        files: list[str] = None, conversation_id: str = None,
                        inputs: dict = None, auto_generate_name: bool = True, 
                        api_base_url: str = 'http://10.144.129.132/v1') -> tuple:
    """
    准备聊天请求所需的headers和payload
    """
    # 上传文件时使用的headers（不包含Content-Type）
    upload_headers = create_headers(api_key)
    
    # 处理文件上传
    uploaded_file_ids = upload_files(api_base_url, upload_headers, files or [], user) if files else []

    # 聊天请求使用的headers（包含Content-Type）
    chat_headers = create_headers(api_key, include_content_type=True)

    # 构建请求payload
    payload = {
        'query': query,
        'response_mode': response_mode.value,
        'user': user,
        'auto_generate_name': auto_generate_name,
        'inputs': inputs if inputs else {},
        'conversation_id': conversation_id if conversation_id else None,
        'files': [{
            'type': 'image',
            'transfer_method': 'local_file',
            'upload_file_id': file_id
        } for file_id in uploaded_file_ids] if uploaded_file_ids else []
    }
    
    chat_messages_url = f'{api_base_url}/chat-messages'
    return chat_headers, payload, chat_messages_url


def handle_blocking_response(headers: dict, payload: dict, chat_messages_url: str) -> str:
    """
    处理阻塞模式的响应
    """
    response = requests.post(chat_messages_url, headers=headers, json=payload)
    dify_api_error_handler(response)
    return response.json()['answer']


def handle_streaming_response(headers: dict, payload: dict, chat_messages_url: str) -> str:
    """
    处理流式响应
    """
    response = requests.post(chat_messages_url, headers=headers, json=payload, stream=True)
    dify_api_error_handler(response)
    
    answer_list = []
    buffer = ""
    
    for chunk in response.iter_content(chunk_size=32):
        if chunk:
            buffer += chunk.decode('utf-8')
            
            while '\n' in buffer:
                line, buffer = buffer.split('\n', 1)
                line = line.strip()
                
                if not line:
                    continue
                    
                if line.startswith('data: '):
                    line = line[6:]
                    
                try:
                    data = json.loads(line)
                    if data['event'] == 'message' and 'answer' in data:
                        print(data['answer'], end='', flush=True)
                        answer_list.append(data['answer'])
                except json.JSONDecodeError:
                    continue
    
    return ''.join(answer_list)


def call_dify_api(api_key: str, query: str, response_mode: ResponseMode, user: str, 
                  files: list[str] = None, conversation_id: str = None,
                  inputs: dict = None, auto_generate_name: bool = True, 
                  api_base_url: str = 'http://10.144.129.132/v1') -> str:
    """
    调用 Dify API 的主函数
    
    参数:
        api_key (str): API 密钥，用于授权
        query (str): 用户的查询或消息内容
        response_mode (ResponseMode): 响应模式（STREAMING 或 BLOCKING）
        user (str): 用户标识
        files (list[str], optional): 要上传的本地文件路径列表
        conversation_id (str, optional): 会话ID
        inputs (dict, optional): 额外的输入参数
        auto_generate_name (bool, optional): 是否自动生成标题
        api_base_url (str, optional): API的基础URL

    返回:
        str: API的响应内容
    """
    headers, payload, chat_messages_url = prepare_chat_request(
        api_key, query, response_mode, user, files, 
        conversation_id, inputs, auto_generate_name, api_base_url
    )

    if response_mode == ResponseMode.BLOCKING:
        return handle_blocking_response(headers, payload, chat_messages_url)
    elif response_mode == ResponseMode.STREAMING:
        return handle_streaming_response(headers, payload, chat_messages_url)
    else:
        raise DifyAPIError(f"不支持的响应模式: {response_mode}")


if __name__ == "__main__":
    files=["/home/lius/图片/必应-加龙河上的历史通道-SergiyN-Getty Images.jpg"]
    query = "你是一个中文ai助手。请以图片中的内容为素材，写一首抒情七言律诗。"
    call_dify_api(
        api_key="app-KAQhWhzyPsBZmlpb0qcyzJcM",
        query=query, 
        response_mode=ResponseMode.STREAMING,
        user="lius",
        files=files
    )
    
    print("-------------------------------------")
    

    