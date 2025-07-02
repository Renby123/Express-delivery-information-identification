import sys
import json
import serial
import time
import cv2
import numpy as np
import io
import easyocr
from PIL import Image
import re
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                            QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                            QTableWidget, QTableWidgetItem, QMessageBox)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QFont


class OCRThread(QThread):
    """后台线程用于处理串口通信和OCR识别"""
    new_data = pyqtSignal(dict)  # 信号：发送新识别的收件人信息
    status_updated = pyqtSignal(str)  # 信号：更新状态信息
    
    def __init__(self, serial_port, baudrate):
        super().__init__()
        self.serial_port = serial_port
        self.baudrate = baudrate
        self.running = True
    
    def run(self):
        try:
            ser = serial.Serial(self.serial_port, self.baudrate, timeout=1)
            self.status_updated.emit(f"串口已连接: {self.serial_port}")
            
            while self.running:
                if ser.in_waiting:
                    # 读取图像大小
                    size_bytes = ser.read(4)
                    if len(size_bytes) < 4:
                        continue
                    
                    img_size = int.from_bytes(size_bytes, 'little')
                    
                    # 读取图像数据
                    img_data = bytearray()
                    start_time = time.time()
                    
                    while len(img_data) < img_size:
                        # 超时处理
                        if time.time() - start_time > 5:  # 5秒超时
                            self.status_updated.emit("接收图像超时")
                            break
                        
                        remaining = img_size - len(img_data)
                        chunk = ser.read(min(2048, remaining))
                        if not chunk:
                            time.sleep(0.01)  # 短暂休眠避免CPU占用过高
                            continue
                        img_data.extend(chunk)
                    
                    if len(img_data) == img_size:
                        self.status_updated.emit(f"接收到图像: {img_size} 字节")
                        
                        # 读取运单号
                        try:
                            # 假设运单号是13字节的ASCII字符串
                            waybill = ser.read(13).decode('ascii')
                            self.status_updated.emit(f"接收到运单号: {waybill}")
                        except Exception as e:
                            waybill = "读取失败"
                            self.status_updated.emit(f"运单号读取错误: {str(e)}")
                        
                        # 转换并处理图像
                        try:
                            image_stream = io.BytesIO(img_data)
                            image = Image.open(image_stream)
                            img_cv = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
                            
                            # 进行OCR识别
                            result = self.perform_ocr(img_cv)
                            result["waybill"] = waybill  # 添加运单号到结果中
                            self.new_data.emit(result)
                            self.status_updated.emit("OCR识别完成")
                            
                        except Exception as e:
                            self.status_updated.emit(f"图像处理错误: {str(e)}")
                    else:
                        self.status_updated.emit(f"图像数据不完整: {len(img_data)}/{img_size} 字节")
            
            ser.close()
            self.status_updated.emit("串口已关闭")
            
        except Exception as e:
            self.status_updated.emit(f"串口连接错误: {str(e)}")
    
    def perform_ocr(self, image):
        """执行OCR识别"""
        try:
            reader = easyocr.Reader(['ch_sim', 'en'])
            results = reader.readtext(image, detail=0)
            text = "\n".join(results)
            
            # 提取姓名（电话号码后第一个中文字符）
            phone_pattern = re.compile(r'(\d{11})转(\d{4})|1[3-9]\d{9}')
            phone_match = phone_pattern.search(text)
            first_chinese_pattern = re.compile(r'[\u4e00-\u9fa5]')
            first_chinese_match = 0

            if phone_match:
                phone_end = phone_match.end()
                text_after_phone = text[phone_end:]
                first_chinese_match = first_chinese_pattern.search(text_after_phone)
            else:
                first_chinese_match = first_chinese_pattern.search(text)

            if first_chinese_match:
                name = first_chinese_match.group(0) + "*"
                
            full_phone = phone_match.group(0) if phone_match else "未找到电话"
            
            return {
                "name": name,
                "phone": full_phone,
                "text": text,
                "waybill": "未提供"  # 默认值，将在主程序中被实际运单号覆盖
            }
        except Exception as e:
            return {
                "name": f"OCR错误: {str(e)}",
                "phone": "OCR错误",
                "text": "",
                "waybill": "未提供"
            }
    
    def stop(self):
        """停止线程"""
        self.running = False
        self.wait()


class ExpressTracker(QMainWindow):
    """快递收件人信息管理主窗口"""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("快递收件人信息管理系统")
        self.setGeometry(0, 60, 480, 270)
        
        # 数据存储
        self.data_file = "express_data.json"
        # 清空文件内容
        with open(self.data_file, 'w', encoding='utf-8') as f:
            pass
        self.data = []
    
        # 初始化UI
        self.init_ui()
        
        # 初始化OCR线程
        self.ocr_thread = OCRThread('/dev/ttyAMA1', 115200)  # 根据实际情况修改串口号和波特率
        self.ocr_thread.new_data.connect(self.add_new_data)
        self.ocr_thread.status_updated.connect(self.update_status)
        self.ocr_thread.start()
    
    def init_ui(self):
        """初始化用户界面"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        
        # 搜索区域
        search_layout = QHBoxLayout()
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入姓名、电话或运单号搜索...")
        
        self.search_button = QPushButton("搜索")
        self.search_button.clicked.connect(self.search_data)
        
        search_layout.addWidget(self.search_input)
        search_layout.addWidget(self.search_button)
        
        # 表格区域
        self.table = QTableWidget()
        self.table.setColumnCount(3)  # 增加一列显示运单号
        self.table.setHorizontalHeaderLabels(["收件人姓名", "收件人电话", "运单号"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        
        # 状态区域
        self.status_label = QLabel("等待数据...")
        
        # 添加到主布局
        main_layout.addLayout(search_layout)
        main_layout.addWidget(self.table)
        main_layout.addWidget(self.status_label)
        
        # 加载数据到表格
        self.load_data_to_table()
    
    def load_data(self):
        """从文件加载数据"""
        try:
            with open(self.data_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []
    
    def save_data(self):
        """保存数据到文件"""
        with open(self.data_file, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
    
    def load_data_to_table(self, data=None):
        """加载数据到表格"""
        if data is None:
            data = self.data
        
        self.table.setRowCount(len(data))
        for row, item in enumerate(data):
            self.table.setItem(row, 0, QTableWidgetItem(item.get('name', '')))
            self.table.setItem(row, 1, QTableWidgetItem(item.get('phone', '')))
            self.table.setItem(row, 2, QTableWidgetItem(item.get('waybill', '')))  # 显示运单号
        
        self.status_label.setText(f"共 {len(data)} 条记录")
    
    @pyqtSlot(dict)
    def add_new_data(self, result):
        """添加新的OCR识别结果到表格"""
        name = result.get('name', '未知姓名')
        phone = result.get('phone', '未知电话')
        waybill = result.get('waybill', '未知运单号')
        
        # 检查是否已有相同记录（包括运单号）
        for row in range(self.table.rowCount()):
            if (self.table.item(row, 0).text() == name and 
                self.table.item(row, 1).text() == phone and
                self.table.item(row, 2).text() == waybill):
                self.status_label.setText("已存在相同记录")
                return
        
        # 添加到数据列表
        new_entry = {
            "name": name,
            "phone": phone,
            "waybill": waybill
        }
        self.data.append(new_entry)
        self.save_data()
        
        # 添加到表格
        row_position = self.table.rowCount()
        self.table.insertRow(row_position)
        self.table.setItem(row_position, 0, QTableWidgetItem(name))
        self.table.setItem(row_position, 1, QTableWidgetItem(phone))
        self.table.setItem(row_position, 2, QTableWidgetItem(waybill))  # 添加运单号到表格
        
        # 滚动到底部
        self.table.scrollToBottom()
        
        self.status_label.setText(f"添加新记录: {name} - {phone} - {waybill}")
    
    @pyqtSlot(str)
    def update_status(self, message):
        """更新状态信息"""
        self.status_label.setText(message)
    
    def search_data(self):
        """搜索数据"""
        search_text = self.search_input.text().strip().lower()
        if not search_text:
            self.load_data_to_table()
            return
        
        results = []
        for item in self.data:
            if (search_text in item.get('name', '').lower() or 
                search_text in item.get('phone', '').lower() or
                search_text in item.get('waybill', '').lower()):  # 支持运单号搜索
                results.append(item)
        
        self.load_data_to_table(results)
        if results:
            self.status_label.setText(f"找到 {len(results)} 条匹配记录")
        else:
            self.status_label.setText("未找到匹配记录")
    
    def closeEvent(self, event):
        """窗口关闭时停止线程"""
        self.ocr_thread.stop()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle('Fusion')  # 使用Fusion风格，在不同平台上外观更一致
    
    # 设置中文字体
    font = QFont("SimHei")
    app.setFont(font)
    
    window = ExpressTracker()
    window.show()
    sys.exit(app.exec())
