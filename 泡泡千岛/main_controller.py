import subprocess
import time
import os

class MuMuAutomation:
    def __init__(self, mumu_path, adb_id="127.0.0.1:16384"):
        self.mumu_path = mumu_path
        self.adb_id = adb_id

    def start_emulator(self):
        """启动 MuMu 模拟器"""
        subprocess.Popen(self.mumu_path)
        # 循环检查 ADB 连接状态
        while True:
            res = subprocess.run(f"adb connect {self.adb_id}", capture_output=True, shell=True)
            if b"connected" in res.stdout: break
            time.sleep(5)

    def toggle_postern(self, state="ON"):
        """通过 ADB 启动或关闭 Postern VPN"""
        # 启动 Postern
        subprocess.run(f"adb -s {self.adb_id} shell am start -n com.tunnel.postern/.PosternMainActivity", shell=True)
        time.sleep(2)
        # 根据坐标点击 VPN 开关（坐标需根据具体分辨率调整）
        # subprocess.run(f"adb -s {self.adb_id} shell input tap 100 200", shell=True)

    def interact_app(self, package_name):
        """打开目标 App 并执行互动逻辑"""
        subprocess.run(f"adb -s {self.adb_id} shell am start -n {package_name}", shell=True)
        time.sleep(10) # 等待启动
        # 执行滑动或点击动作
        subprocess.run(f"adb -s {self.adb_id} shell input swipe 500 800 500 200", shell=True)

    def run_cycle(self):
        """执行完整自动化任务循环"""
        self.start_emulator()
        self.toggle_postern("ON")
        self.interact_app("com.target.app/com.target.main.Activity")
        # 互动完成后，读取 addon.py 生成的 results.jsonl 进行后处理