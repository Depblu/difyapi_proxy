import json
import requests
import os
from enum import Enum


dify_url = "http://10.144.129.132/v1"


class DifyAPIError(Exception):
    """自定义异常类用于处理 Dify API 错误"""
    pass


class ResponseMode(Enum):
    STREAMING = "streaming"  # 流式模式（推荐）。基于 SSE（Server-Sent Events）实现类似打字机输出方式的流式返回。
    BLOCKING = "blocking"    # 阻塞模式，等待执行完毕后返回结果。（请求若流程较长可能会被中断）。由于 Cloudflare 限制，请求会在 100 秒超时无返回后中断。
    

def dify_api_error_handler(response):
    if response.status_code != 200:
            try:
                error_info = response.json()
                error_code = error_info.get('code', 'unknown_error')
                error_message = error_info.get('message', '未知错误')
            except ValueError:
                error_code = 'unknown_error'
                error_message = response.text or '未知错误'
            raise DifyAPIError(f"发送聊天消息失败: {error_code} - {error_message}")
        
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


def call_dify_api(api_key:str, query:str, response_mode:ResponseMode, user:str, files:list[str]=None, conversation_id:str=None, 
                  inputs:dict=None, auto_generate_name:bool=True, api_base_url:str='http://10.144.129.132/v1'):
    """
    调用 Dify API 以上传文件并发送聊天消息。

    参数:
        api_key (str): API 密钥，用于授权。
        query (str): 用户的查询或消息内容。
        user (str): 用户标识，用于定义终端用户的身份。
        files (list of str, optional): 要上传的本地文件路径列表。支持图片格式（png, jpg, jpeg, webp, gif）。
        conversation_id (str, optional): 会话 ID，用于基于之前的聊天记录继续对话。
        inputs (dict, optional): 额外的输入参数。
        auto_generate_name (bool, optional): 是否自动生成标题，默认 True。
        api_base_url (str, optional): API 的基础 URL，默认 'http://10.144.129.132/v1'。

    返回:
        dict: /chat-messages API 的响应内容。

    抛出:
        DifyAPIError: 如果 API 调用失败或返回错误。
    """
    headers = {
        'Authorization': f'Bearer {api_key}'
    }

    uploaded_file_ids = []

    # 文件上传部分
    if files:
        uploaded_file_ids = upload_files(api_base_url, headers, files or [], user)

    # 构建 /chat-messages 请求体
    chat_messages_url = f'{api_base_url}/chat-messages'
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
        
    headers['Content-Type'] = 'application/json'

    if response_mode == ResponseMode.BLOCKING:
        response = requests.post(chat_messages_url, headers=headers, json=payload)
        dify_api_error_handler(response)
        return response.json()['answer']
    elif response_mode == ResponseMode.STREAMING:
        response = requests.post(chat_messages_url, headers=headers, json=payload, stream=True)
        dify_api_error_handler(response)
        
        answer_list = []
        buffer = ""
        
        for chunk in response.iter_content(chunk_size=32):
            if chunk:
                # 将字节转换为字符串并添加到缓冲区
                buffer += chunk.decode('utf-8')
                
                # 检查缓冲区中是否有完整的数据行
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    line = line.strip()
                    
                    # 跳过空行
                    if not line:
                        continue
                        
                    # 移除 "data: " 前缀
                    if line.startswith('data: '):
                        line = line[6:]
                        
                    try:
                        data = json.loads(line)
                        if data['event'] == 'message':
                            if 'answer' in data:
                                print(data['answer'], end='', flush=True)
                                answer_list.append(data['answer'])
                    except json.JSONDecodeError:
                        continue
        
        return ''.join(answer_list)
    else:
        raise DifyAPIError(f"不支持的响应模式: {response_mode}")
        




if __name__ == "__main__":
    files=["/home/lius/图片/必应-加龙河上的历史通道-SergiyN-Getty Images.jpg"]
    query = "你是一个中文ai助手。请以图片中的内容为素材，写一首抒情诗。"
    #print(call_dify_api(api_key="app-KAQhWhzyPsBZmlpb0qcyzJcM", query="你是一个中文ai助手，请以问八百标兵奔北坡为标题，写一首抒情诗。", response_mode=ResponseMode.STREAMING, user="lius", files=[]))
    print(call_dify_api(api_key="app-KAQhWhzyPsBZmlpb0qcyzJcM", query=query, 
                        response_mode=ResponseMode.STREAMING, user="lius", files=files))
    