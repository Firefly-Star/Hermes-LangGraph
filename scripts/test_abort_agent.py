"""
实验：中断流式请求后，能否立即在同一 conversation 发新请求？

流程：
1. 给 agent 发一个长输出请求（如"写一篇 500 字作文"），流式读取
2. 收到几块输出后，客户端主动断开连接（resp.close()）
3. 立即向同一 agent + 同一 conversation 发第二个请求
4. 观察：
   - 第二个请求是否被阻塞（gateway 卡在上一个请求里）
   - 第二个请求是否返回正常
   - 第一个请求的后续输出是否还在打印

用法：先确保 Hermes Gateway 在运行（工作流 dev agent port=8644）
"""
import requests, json, time, sys

PORT = 8644        # dev agent
API_KEY = "kaguya"
CONVERSATION = f"test-abort-{int(time.time())}"  # 唯一对话名

def send_long_request():
    """发一个期待长输出的流式请求。"""
    resp = requests.post(
        f"http://127.0.0.1:{PORT}/v1/responses",
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json={"input": "请写一篇 500 字的作文，主题是春天的景色。请一行一行地输出，每行后面加一个换行。",
              "conversation": CONVERSATION, "stream": True},
        stream=True, timeout=(10, None),
    )
    if resp.status_code != 200:
        print(f"[!] 请求失败: HTTP {resp.status_code} {resp.text[:200]}")
        resp.close()
        return None
    return resp

def drain(resp, max_chunks=5):
    """读取 max_chunks 块 SSE 数据后停止。"""
    count = 0
    for line in resp.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        try:
            data = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        if data.get("type") == "response.output_text.delta":
            delta = data.get("delta", "")
            print(delta, end="", flush=True)
            count += 1
            if count >= max_chunks:
                print("\n[中断] 已收到 {max_chunks} 块输出，主动断开...")
                return True
        elif data.get("type") == "response.complete":
            print("\n[完成] 请求正常结束")
            return False
    return False

def send_quick_request():
    """发一个简单请求测试同一 conversation 是否可用。"""
    print("\n----- 发送第二个请求（同一 conversation）-----")
    resp = requests.post(
        f"http://127.0.0.1:{PORT}/v1/responses",
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json={"input": "回复三个字：你好",
              "conversation": CONVERSATION, "stream": True},
        stream=True, timeout=(10, None),
    )
    if resp.status_code != 200:
        print(f"[!] 第二个请求失败: HTTP {resp.status_code}")
        resp.close()
        return False

    print("[第二个请求] 回复内容:")
    full_text = ""
    for line in resp.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        try:
            data = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        if data.get("type") == "response.output_text.delta":
            txt = data.get("delta", "")
            full_text += txt
            print(txt, end="", flush=True)
        elif data.get("type") == "response.complete":
            break

    resp.close()
    print(f"\n[第二个请求] 完成，收到 {len(full_text)} 字")
    return len(full_text) > 0

# ── 主流程 ──
print(f"对话名: {CONVERSATION}")

# Step 1: 发长请求
print("[1] 发送第一个请求（长输出）...")
resp1 = send_long_request()
if not resp1:
    sys.exit(1)

# Step 2: 读几块后中断
print("[2] 读取输出中...")
aborted = drain(resp1, max_chunks=5)

# Step 3: 关闭连接
resp1.close()
print("[3] 第一个请求的连接已关闭")

if aborted:
    time.sleep(0.5)  # 给 gateway 一点点时间处理断连

# Step 4: 立即发第二个请求
print("[4] 测试同一 conversation 是否可用...")
ok = send_quick_request()

# 结论
print(f"\n{'='*50}")
if ok:
    print("结论: ✓ 中断后同一 conversation 可立即复用，gateway 没有阻塞")
else:
    print("结论: ✗ 中断后同一 conversation 被阻塞，gateway 未正确处理断连")
print(f"{'='*50}")
