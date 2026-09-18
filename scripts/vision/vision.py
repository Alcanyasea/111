# -*- coding: utf-8 -*-
# ============================================================
# vision.py — MAA 同款屏幕识别助手（模板匹配 + PaddleOCR）
# 算法与 MAA src/MaaCore/Vision/Matcher.cpp 一致：
#   cv2.matchTemplate(TM_CCOEFF_NORMED) + minMaxLoc + ROI + 阈值
# OCR 为 MAA 同款 PaddleOCR det/rec ONNX 模型（models/ 目录，
# 拷自 MAA resource/PaddleOCR，与官服/B服安装完全一致）。
#
# 模板库 vision/templates/ 拷自 MAA resource/template（720p 基准），
# manifest.json 定义每个模板的 roi 与 templThreshold（取自 MAA tasks.json）。
#
# 用法一（单帧）：vision.py --image frame.png --ops ops.json
#   ops.json: {
#     "templates": [{"name":"StartToWakeUp","roi":[x,y,w,h],"threshold":0.7}],
#     "ocr":       [{"name":"login","text":["账号登录"],"exact":false,"roi":null}]
#   }
#   输出（stdout 单行 JSON）：
#   {"templates":{"StartToWakeUp":{"matched":true,"score":0.93,"x":537,"y":480,"w":202,"h":65}},
#    "ocr":{"login":{"matched":true,"hit":"账号登录","box":[..],"lines":[{"text":"..","box":[..]}]}}}
#   ocr 项无 text 时（"all": true）返回全部行，不做匹配。
#
# 用法二（常驻）：vision.py --serve
#   stdin 每行一个请求 JSON（同 ops.json 外加 "image": 路径），
#   stdout 每行一个响应 JSON。模型只加载一次，供轮询循环复用。
# 退出码：0 正常响应；2 请求处理出错（响应里带 "error"）
# ============================================================
import argparse
import json
import math
import pathlib
import sys

import cv2
import numpy as np

BASE = pathlib.Path(__file__).resolve().parent
TEMPLATES_DIR = BASE / "templates"
MANIFEST_PATH = TEMPLATES_DIR / "manifest.json"
DET_MODEL = BASE / "models" / "det" / "inference.onnx"
REC_MODEL = BASE / "models" / "rec" / "inference.onnx"
KEYS_FILE = BASE / "models" / "rec" / "keys.txt"

DET_LIMIT_SIDE = 960        # det 输入长边上限（paddle mobile 默认）
# MAA OnnxHelper::image_to_tensor 的预处理：BGR→RGB，仅除以 255，无 mean/std
DET_THRESH = 0.3            # DB 概率图二值化
DET_BOX_THRESH = 0.45       # 候选框内平均分过滤
DET_UNCLIP_RATIO = 1.6      # DB 外扩比例（paddle v3 mobile 默认）
REC_IMG_H = 48              # rec 输入高（PP-OCRv3）


class Ocr:
    """PaddleOCR det + rec（onnxruntime CPU），模型懒加载、进程内复用"""

    def __init__(self):
        self._ready = False
        self._det = None
        self._rec = None
        self._keys = None

    def _init(self):
        if self._ready:
            return
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 4
        self._det = ort.InferenceSession(str(DET_MODEL), sess_options=opts,
                                         providers=["CPUExecutionProvider"])
        self._rec = ort.InferenceSession(str(REC_MODEL), sess_options=opts,
                                         providers=["CPUExecutionProvider"])
        with open(KEYS_FILE, encoding="utf-8") as f:
            self._keys = [line.rstrip("\r\n") for line in f if line.strip()]
        self._ready = True

    # ---- det：DBNet 文本检测，返回行级文本框（原图坐标，4 点）----
    def detect(self, img):
        self._init()
        h, w = img.shape[:2]
        scale = min(1.0, DET_LIMIT_SIDE / max(h, w))
        rh, rw = int(round(h * scale)), int(round(w * scale))
        # 补到 32 的倍数
        ph, pw = math.ceil(rh / 32) * 32, math.ceil(rw / 32) * 32
        canvas = np.zeros((ph, pw, 3), np.float32)
        small = cv2.cvtColor(cv2.resize(img, (rw, rh)), cv2.COLOR_BGR2RGB)
        canvas[:rh, :rw, :] = small
        x = (canvas / 255.0)
        x = x.transpose(2, 0, 1)[None].astype(np.float32)
        inp = self._det.get_inputs()[0].name
        prob = self._det.run(None, {inp: x})[0][0, 0]          # (ph, pw)
        bitmap = (prob > DET_THRESH).astype(np.uint8)
        boxes = []
        contours, _ = cv2.findContours(bitmap, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            if cv2.contourArea(cnt) < 8:
                continue
            (cx, cy), (bw, bh), ang = cv2.minAreaRect(cnt)
            if min(bw, bh) < 3:
                continue
            # 分数在原始轮廓内取平均（此模型概率图硬饱和，外扩框会被纯黑背景稀释）
            if self._contour_score(prob, cnt) < DET_BOX_THRESH:
                continue
            # DB unclip：矩形外扩 d = area*ratio/perimeter（只用于框几何）
            d = (bw * bh) * DET_UNCLIP_RATIO / (2 * (bw + bh))
            box = cv2.boxPoints(((cx, cy), (bw + 2 * d, bh + 2 * d), ang))
            # 缩放回原图坐标
            box = box / [scale, scale]
            boxes.append(np.clip(box, 0, [w - 1, h - 1]).astype(np.float32))
        # 阅读序：先上后下、行内先左后右
        boxes.sort(key=lambda b: (min(p[1] for p in b) // 12, min(p[0] for p in b)))
        return boxes

    @staticmethod
    def _contour_score(prob, cnt):
        h, w = prob.shape
        mask = np.zeros((h, w), np.uint8)
        cv2.drawContours(mask, [cnt], -1, 1, -1)
        v = prob[mask > 0]
        return float(v.mean()) if v.size else 0.0

    # ---- rec：CRNN 识别单个文本框 ----
    def recognize(self, img, box):
        self._init()
        crop = self._rot_crop(img, box)
        if crop.size == 0:
            return ""
        ch, cw = crop.shape[:2]
        rw = max(8, int(round(cw * REC_IMG_H / ch)))
        rw = min(rw, 960)
        resized = cv2.resize(crop, (rw, REC_IMG_H))
        pw = math.ceil(rw / 32) * 32
        canvas = np.zeros((REC_IMG_H, pw, 3), np.float32)
        canvas[:, :rw, :] = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        x = (canvas / 255.0)
        x = x.transpose(2, 0, 1)[None].astype(np.float32)
        inp = self._rec.get_inputs()[0].name
        out = self._rec.run(None, {inp: x})[0]                 # (1, T, C)
        ids = out[0].argmax(axis=-1)
        chars = []
        prev = 0
        for i in ids:
            if i != prev and i > 0:
                if i - 1 < len(self._keys):
                    chars.append(self._keys[i - 1])
            prev = i
        return "".join(chars)

    @staticmethod
    def _rot_crop(img, box):
        pts = np.asarray(box, np.float32)
        # boxPoints 角点顺序不固定，统一排成 左上/右上/右下/左下，防止裁剪倒置
        s = pts.sum(axis=1)
        d = (pts[:, 1] - pts[:, 0])
        tl, br = pts[np.argmin(s)], pts[np.argmax(s)]
        tr, bl = pts[np.argmin(d)], pts[np.argmax(d)]
        pts = np.array([tl, tr, br, bl], np.float32)
        w = int(max(np.linalg.norm(pts[0] - pts[1]), np.linalg.norm(pts[2] - pts[3])))
        h = int(max(np.linalg.norm(pts[0] - pts[3]), np.linalg.norm(pts[1] - pts[2])))
        if w < 2 or h < 2:
            return np.array([])
        dst = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32)
        m = cv2.getPerspectiveTransform(pts, dst)
        return cv2.warpPerspective(img, m, (w, h))

    # ---- 全帧 OCR：返回 [{text, box:[x,y,w,h], center:[x,y]}] ----
    def lines(self, img):
        out = []
        for box in self.detect(img):
            text = self.recognize(img, box).strip()
            if not text:
                continue
            x0, y0 = box[:, 0].min(), box[:, 1].min()
            x1, y1 = box[:, 0].max(), box[:, 1].max()
            out.append({
                "text": text,
                "box": [int(x0), int(y0), int(x1 - x0), int(y1 - y0)],
                "center": [int((x0 + x1) / 2), int((y0 + y1) / 2)],
            })
        return out


class Vision:
    def __init__(self):
        self._ocr = Ocr()
        self._templates = {}     # name -> (img, roi, threshold)

    def _load_manifest(self):
        if self._templates:
            return
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        for entry in manifest:
            img = cv2.imread(str(TEMPLATES_DIR / entry["file"]), cv2.IMREAD_COLOR)
            if img is None:
                raise RuntimeError(f"模板加载失败: {entry['file']}")
            self._templates[entry["name"]] = (
                img, entry.get("roi"), float(entry.get("threshold", 0.7)))

    def match_templates(self, img, wanted):
        self._load_manifest()
        result = {}
        for item in wanted:
            name = item["name"]
            if name not in self._templates:
                result[name] = {"error": "模板不存在"}
                continue
            templ, default_roi, threshold = self._templates[name]
            roi = item.get("roi") or default_roi
            score_img = img
            ox = oy = 0
            if roi:
                x, y, rw, rh = roi
                score_img = img[max(0, y):y + rh, max(0, x):x + rw]
                ox, oy = max(0, x), max(0, y)
            if score_img.shape[0] < templ.shape[0] or score_img.shape[1] < templ.shape[1]:
                result[name] = {"matched": False, "score": 0.0}
                continue
            res = cv2.matchTemplate(score_img, templ, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            th = float(item.get("threshold", threshold))
            tml_w, tml_h = templ.shape[1], templ.shape[0]
            result[name] = {
                "matched": bool(max_val >= th),
                "score": round(float(max_val), 4),
                "x": int(max_loc[0] + ox), "y": int(max_loc[1] + oy),
                "w": tml_w, "h": tml_h,
            }
        return result

    def ocr(self, img, wanted):
        result = {}
        lines_cache = None
        for item in wanted:
            roi = item.get("roi")
            scope = img
            ox = oy = 0
            if roi:
                x, y, rw, rh = roi
                scope = img[max(0, y):y + rh, max(0, x):x + rw]
                ox, oy = max(0, x), max(0, y)
            if lines_cache is None or roi is not None:
                lines = self._ocr.lines(scope)
                for ln in lines:
                    ln["center"] = [ln["center"][0] + ox, ln["center"][1] + oy]
                    ln["box"] = [ln["box"][0] + ox, ln["box"][1] + oy,
                                 ln["box"][2], ln["box"][3]]
                if roi is None:
                    lines_cache = lines
            else:
                lines = lines_cache
            texts = [ln["text"] for ln in lines]
            entry = {"matched": False, "lines": lines}
            if item.get("all"):
                result[item["name"]] = entry
                continue
            exact = bool(item.get("exact"))
            for marker in item.get("text", []):
                for ln in lines:
                    ok = (ln["text"] == marker) if exact else (marker in ln["text"])
                    if ok:
                        entry.update({"matched": True, "hit": marker, "box": ln["box"],
                                      "center": ln["center"], "text": ln["text"]})
                        break
                if entry["matched"]:
                    break
            result[item["name"]] = entry
        return result

    def process(self, image_path, ops):
        img = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError(f"图片读取失败: {image_path}")
        out = {}
        if ops.get("templates"):
            out["templates"] = self.match_templates(img, ops["templates"])
        if ops.get("ocr"):
            out["ocr"] = self.ocr(img, ops["ocr"])
        return out


def _emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image")
    ap.add_argument("--ops")
    ap.add_argument("--serve", action="store_true")
    args = ap.parse_args()
    vision = Vision()
    if args.serve:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                _emit(vision.process(req["image"], req))
            except Exception as e:                     # 单帧出错不退出服务
                _emit({"error": str(e)})
        return
    if not args.image or not args.ops:
        ap.error("需要 --image/--ops 或 --serve")
    ops = json.loads(pathlib.Path(args.ops).read_text(encoding="utf-8"))
    _emit(vision.process(args.image, ops))


if __name__ == "__main__":
    main()
