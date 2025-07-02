import sensor, image, time, pyb
led = pyb.LED(3)

# 摄像头初始化
sensor.reset()
sensor.set_pixformat(sensor.RGB565)
sensor.set_framesize(sensor.VGA)
sensor.skip_frames(time = 2000)
sensor.set_auto_gain(False)
sensor.set_auto_whitebal(False)

uart = pyb.UART(3, 115200)  # 初始化串口，波特率115200
clock = time.clock()
pre_code=[]
print("系统初始化完成")
while(True):
    led.off()
    clock.tick()
    img = sensor.snapshot()
    img.lens_corr(1.5)
    barcodes = img.copy(roi=(0,0,640,100)).to_grayscale().find_barcodes()
    if barcodes:
        code = barcodes[0].payload()
        if (code in pre_code) or (len(code)!=13):
            continue
        print("识别到条形码：",code)
        y_m = barcodes[0].y()+barcodes[0].h()
        img_compressed = img.scale(roi=(0,y_m,640,200)).compress(quality=35)
        size_bytes = len(img_compressed).to_bytes(4, 'little')
        pre_code.append(code)
        uart.write(size_bytes)
        uart.write(img_compressed)
        uart.write(code)
        led.on()
    #print(clock.fps())