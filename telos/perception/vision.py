"""视觉感知通道

采集摄像头帧 → 压缩 → Base64编码 → 供 LLM 分析
"""

import base64
import io
from PIL import Image


class VisionChannel:
    """摄像头视觉输入"""

    name = "vision"
    priority = 0  # 最高优先级

    def __init__(self, camera_id: int = 0, resolution: tuple = (640, 480),
                 quality: int = 70):
        self.camera_id = camera_id
        self.resolution = resolution
        self.quality = quality  # JPEG 压缩质量
        self._active = False
        self._cap = None

    def start(self) -> bool:
        # try:
        #     import cv2
        #     self._cap = cv2.VideoCapture(self.camera_id)
        #     self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.resolution[0])
        #     self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.resolution[1])
        #     self._active = self._cap.isOpened()
        #     return self._active
        # except ImportError:
        #     return False
        return False  # 默认不启用，需安装 opencv

    def stop(self) -> None:
        self._active = False
        if self._cap:
            self._cap.release()
            self._cap = None

    def capture(self) -> dict:
        # if self._cap and self._active:
        #     ret, frame = self._cap.read()
        #     if ret:
        #         img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        #         return self._encode(img)
        return {"error": "摄像头未激活"}

    def _encode(self, image: Image.Image) -> dict:
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=self.quality)
        return {
            "image_b64": base64.b64encode(buf.getvalue()).decode("utf-8"),
            "width": image.width,
            "height": image.height,
        }

    def health(self) -> dict:
        return {"name": "vision", "active": self._active,
                "camera_id": self.camera_id}

    # 兼容非摄像头输入（从文件/URL 加载图片）
    @staticmethod
    def load_image(path: str, quality: int = 70) -> dict:
        img = Image.open(path).convert("RGB")
        ch = VisionChannel(quality=quality)
        return ch._encode(img)
