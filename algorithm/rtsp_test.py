import cv2

RTSP_URL = "rtsp://localhost:8554/test"

cap = cv2.VideoCapture(RTSP_URL)

if not cap.isOpened():
    print("无法打开视频流:", RTSP_URL)
    exit(1)

print("视频流打开成功，按 q 退出")

while True:
    ret, frame = cap.read()

    if not ret:
        print("读取视频帧失败")
        break

    cv2.imshow("RTSP Test", frame)

    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()