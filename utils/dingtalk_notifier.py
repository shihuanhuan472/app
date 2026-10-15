import requests
import json
from datetime import datetime, timedelta
from functools import lru_cache

# ================== 钉钉企业内部应用配置 ==================
APP_KEY = "dingo1gu4inpkrgjerls"
APP_SECRET = "GsyUSdokdGHAdOfEygm46WC7ard6PvkMJCNSYrqaniv-wxbQk6Cw2T7vJRMOJ1ka"

# ================== 钉钉 Token 缓存 ==================
_token_cache = {
    "access_token": None,
    "expire_time": None
}

def get_access_token():
    """获取钉钉企业内部应用的access_token，并自动缓存"""
    global _token_cache
    now = datetime.now()
    if _token_cache["access_token"] and _token_cache["expire_time"] and now < _token_cache["expire_time"]:
        return _token_cache["access_token"]

    url = "https://oapi.dingtalk.com/gettoken"
    params = {
        "appkey": APP_KEY,
        "appsecret": APP_SECRET
    }
    resp = requests.get(url, params=params, timeout=5)
    if resp.status_code == 200:
        data = resp.json()
        if data.get("errcode") == 0:
            token = data["access_token"]
            expires_in = data.get("expires_in", 7200)
            _token_cache["access_token"] = token
            _token_cache["expire_time"] = now + timedelta(seconds=expires_in - 300)
            return token
        else:
            raise Exception(f"获取access_token失败: {data}")
    else:
        raise Exception(f"请求token失败: {resp.text}")

# ================== 根据手机号获取 userid ==================
@lru_cache(maxsize=128)
def get_userid_by_mobile(mobile):
    """根据手机号获取钉钉userid（结果缓存）"""
    token = get_access_token()
    if not token:
        print(f"获取token失败，无法获取手机号 {mobile} 的userid")
        return None

    url = "https://oapi.dingtalk.com/topapi/v2/user/getbymobile"
    headers = {"Content-Type": "application/json"}
    params = {"access_token": token}
    body = {"mobile": mobile}

    resp = requests.post(url, json=body, params=params, headers=headers, timeout=5)
    if resp.status_code == 200:
        data = resp.json()
        if data.get("errcode") == 0:
            return data["result"]["userid"]
        else:
            print(f"获取userid失败 (手机号{mobile}): {data}")
            return None
    else:
        print(f"请求userid接口失败: {resp.text}")
        return None


# ================== 发送钉钉消息 ==================
def send_dingtalk_message(title, text, mobiles):
    """
    通过钉钉机器人发送单聊消息
    
    参数:
        title: 消息标题（必填）
        text: 消息内容（必填）
        mobiles: 接收人手机号列表（必填），支持以下格式：
                - 列表: ["13632720147", "13800138000"]
                - 字符串: "13632720147" (单个手机号)
    
    返回:
        bool: 发送成功返回 True，失败返回 False
    
    示例:
        # 发送给单个用户
        send_dingtalk_message(
            "系统通知",
            "服务器重启完成",
            "13632720147"
        )
        
        # 发送给多个用户
        send_dingtalk_message(
            "系统告警",
            "CPU使用率超过90%",
            ["13632720147", "13800138000"]
        )
    """
    # 统一处理手机号格式
    if isinstance(mobiles, str):
        mobiles = [mobiles]
    elif not isinstance(mobiles, list):
        print(f"❌ 参数 mobiles 格式错误，应为字符串或列表，实际为: {type(mobiles)}")
        return False
    
    if not mobiles:
        print("❌ 接收人列表为空，跳过发送")
        return False
    
    print(f"📱 准备发送消息给: {mobiles}")

    # 将手机号转换为userid
    userid_list = []
    invalid_mobiles = []
    for mobile in mobiles:
        userid = get_userid_by_mobile(mobile)
        if userid:
            userid_list.append(userid)
        else:
            invalid_mobiles.append(mobile)
            print(f"⚠️ 手机号 {mobile} 未找到对应的钉钉用户，将忽略")

    if invalid_mobiles:
        print(f"⚠️ 无效手机号: {invalid_mobiles}")
    
    if not userid_list:
        print("❌ 没有有效的接收人userid，放弃发送")
        return False

    # 获取 access_token
    token = get_access_token()
    if not token:
        print("❌ 获取 access_token 失败")
        return False

    # 官方接口：批量发送单聊消息
    url = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"
    headers = {
        "Content-Type": "application/json",
        "x-acs-dingtalk-access-token": token
    }

    # 构造消息内容（不包含任何分级标识）
    msg_content = f"### {title}\n\n{text}\n\n---\n*此消息由钉钉机器人自动发送*"

    # 构造请求体
    payload = {
        "userIds": userid_list,
        "robotCode": APP_KEY,
        "msgKey": "sampleMarkdown",
        "msgParam": json.dumps({
            "text": msg_content
        })
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=5)
        print(f"📡 接口响应状态码: {resp.status_code}")
        
        if resp.status_code == 200:
            data = resp.json()
            # 检查是否有错误
            if "errcode" in data and data["errcode"] != 0:
                print(f"❌ 钉钉消息发送失败: {data}")
                return False
            elif "code" in data and str(data["code"]) != "0":
                print(f"❌ 钉钉消息发送失败: {data}")
                return False
            else:
                # 成功
                print(f"✅ 钉钉消息发送成功！")
                print(f"   - 接收人数量: {len(userid_list)}")
                print(f"   - 手机号: {mobiles}")
                if data.get("processQueryKey"):
                    print(f"   - processQueryKey: {data['processQueryKey']}")
                return True
        else:
            print(f"❌ 请求钉钉接口失败，HTTP状态码: {resp.status_code}")
            print(f"   响应内容: {resp.text}")
            return False
    except Exception as e:
        print(f"❌ 发送钉钉消息异常: {e}")
        return False


# ================== 使用示例 ==================
if __name__ == "__main__":
 
    # 示例1：发送给单个用户
    print("\n【示例1】发送给单个用户:")
    success = send_dingtalk_message(
        "系统通知",
        f"服务器于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 完成例行维护",
        "13632720147"
    )
    
    # 示例2：发送给多个用户
    print("\n【示例2】发送给多个用户:")
    success = send_dingtalk_message(
        "紧急告警",
        "数据库连接池使用率达到95%，请立即检查",
        ["13632720147"]
    )
    
    # 示例3：带时间戳的详细消息
    print("\n【示例3】发送详细报告:")
    success = send_dingtalk_message(
        "每日报告",
        f"""报告时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        
系统状态:
- CPU使用率: 45%
- 内存使用率: 62%
- 磁盘使用率: 78%
- 在线用户: 1,234人

所有服务运行正常。""",
        "13632720147"
    )
