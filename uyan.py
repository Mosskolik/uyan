#!/usr/bin/env python3
"""
UYAN - Uyku / goz kapanma algilayici alarm.

Kamerayi izler; gozlerin belirli bir sureden uzun kapali kalirsa
(ya da istersen yuzun kameradan kaybolursa) kulakliktan / hoparlorden
gittikce yukselen bir alarm calar. Gozlerini birkac saniye acik
tuttugunda alarm kendiliginden susar.

Kullanim:
    python uyan.py                 # varsayilan: 3 sn goz kapali -> alarm
    python uyan.py --sure 5        # 5 sn
    python uyan.py --yuz-yok 15    # yuz 15 sn gorunmezse alarm (vars. 10, 0 = kapali)
    python uyan.py --ses alarm.mp3 # kendi alarm sesin

Tuslar (kamera penceresi seciliyken):
    KAPAT dugmesi / q / ESC : cikis
    SUSTUR dugmesi          : alarm calarken gorunur
    BOSLUK   : alarmi sustur
    c        : yeniden kalibre et
"""

import argparse
import math
import os
import sys
import threading
import time
import urllib.request

import cv2
import numpy as np

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task"
)
# .exe olarak paketlendiyse modeli exe'nin yanina kaydet
_KLASOR = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
MODEL_PATH = os.path.join(_KLASOR, "face_landmarker.task")

# MediaPipe yuz agindaki goz noktalari (p1..p6, EAR formulu icin)
SOL_GOZ = [362, 385, 387, 263, 373, 380]
SAG_GOZ = [33, 160, 158, 133, 153, 144]


# --------------------------------------------------------------------------
# Alarm sesi
# --------------------------------------------------------------------------
class Alarm:
    """Varsayilan ses cikisindan (kulaklik takiliysa kulakliktan) alarm calar."""

    def __init__(self, ses_dosyasi=None, max_ses=1.0):
        self.max_ses = max_ses
        self.aktif = False
        self._thread = None
        self._dur = threading.Event()
        self.pygame = None
        self.ses = None
        try:
            import pygame

            pygame.mixer.init(frequency=44100, size=-16, channels=2)
            self.pygame = pygame
            if ses_dosyasi:
                self.ses = pygame.mixer.Sound(ses_dosyasi)
            else:
                self.ses = pygame.sndarray.make_sound(self._bip_uret())
        except Exception as e:  # pygame yoksa ya da ses aygiti acilamazsa
            print(f"[uyari] pygame ile ses acilamadi ({e}). Yedek bip kullanilacak.")
            self.pygame = None

    @staticmethod
    def _bip_uret(sr=44100):
        """Iki tonlu, kesik kesik, dikkat cekici bir alarm sesi (yaklasik 1 sn)."""
        parcalar = []
        for frek in (880, 1320, 880, 1320):
            t = np.linspace(0, 0.18, int(sr * 0.18), endpoint=False)
            dalga = np.sign(np.sin(2 * np.pi * frek * t)) * 0.6 + np.sin(2 * np.pi * frek * 2 * t) * 0.4
            zarf = np.minimum(1, np.minimum(t, t[-1] - t) * 200)  # tik sesini onle
            parcalar.append(dalga * zarf)
            parcalar.append(np.zeros(int(sr * 0.07)))
        mono = np.concatenate(parcalar)
        stereo = np.column_stack([mono, mono])
        return np.ascontiguousarray((stereo * 32767 * 0.9).astype(np.int16))

    def baslat(self):
        if self.aktif:
            return
        self.aktif = True
        self._dur.clear()
        self._thread = threading.Thread(target=self._calis, daemon=True)
        self._thread.start()

    def durdur(self):
        if not self.aktif:
            return
        self.aktif = False
        self._dur.set()
        if self.pygame:
            self.pygame.mixer.stop()

    def _calis(self):
        baslangic = time.time()
        if self.pygame:
            kanal = self.ses.play(loops=-1)
            while not self._dur.is_set():
                # %30'dan baslayip ~10 sn icinde en yuksek sese cik
                gecen = time.time() - baslangic
                seviye = min(self.max_ses, 0.3 + 0.07 * gecen)
                if kanal is not None:
                    kanal.set_volume(seviye)
                self._dur.wait(0.2)
            if kanal is not None:
                kanal.stop()
        else:
            while not self._dur.is_set():
                if sys.platform.startswith("win"):
                    import winsound

                    winsound.Beep(1000, 300)
                    winsound.Beep(1500, 300)
                else:
                    sys.stdout.write("\a")
                    sys.stdout.flush()
                    self._dur.wait(0.5)


# --------------------------------------------------------------------------
# Goz algilayicilar
# --------------------------------------------------------------------------
def ear_hesapla(noktalar):
    """Goz acikligi orani (Eye Aspect Ratio). Acik goz ~0.25-0.35, kapali ~0.1."""
    p1, p2, p3, p4, p5, p6 = noktalar
    d = lambda a, b: math.dist(a, b)
    yatay = d(p1, p4)
    if yatay == 0:
        return 0.0
    return (d(p2, p6) + d(p3, p5)) / (2.0 * yatay)


class MediaPipeAlgilayici:
    """MediaPipe FaceLandmarker ile hassas goz takibi."""

    ad = "MediaPipe"

    def __init__(self):
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision

        if not os.path.exists(MODEL_PATH):
            print("Yuz modeli indiriliyor (tek seferlik, ~4 MB)...")
            gecici = MODEL_PATH + ".part"
            urllib.request.urlretrieve(MODEL_URL, gecici)
            os.replace(gecici, MODEL_PATH)

        self.mp = mp
        secenekler = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=MODEL_PATH),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=1,
        )
        self.model = vision.FaceLandmarker.create_from_options(secenekler)
        self._son_ts = 0

    def kapat(self):
        try:
            self.model.close()
        except Exception:
            pass

    def olc(self, kare):
        """(yuz_var, goz_acikligi, cizilecek_noktalar) dondurur."""
        h, w = kare.shape[:2]
        rgb = cv2.cvtColor(kare, cv2.COLOR_BGR2RGB)
        goruntu = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb)
        ts = max(self._son_ts + 1, int(time.monotonic() * 1000))
        self._son_ts = ts
        sonuc = self.model.detect_for_video(goruntu, ts)
        if not sonuc.face_landmarks:
            return False, None, []
        lm = sonuc.face_landmarks[0]
        sol = [(lm[i].x * w, lm[i].y * h) for i in SOL_GOZ]
        sag = [(lm[i].x * w, lm[i].y * h) for i in SAG_GOZ]
        ear = (ear_hesapla(sol) + ear_hesapla(sag)) / 2.0
        return True, ear, sol + sag


class HaarAlgilayici:
    """Yedek yontem: OpenCV Haar kaskatlari (internet/model gerektirmez, daha az hassas)."""

    ad = "OpenCV Haar (yedek)"

    def __init__(self):
        yol = cv2.data.haarcascades
        self.yuz = cv2.CascadeClassifier(os.path.join(yol, "haarcascade_frontalface_default.xml"))
        self.goz = cv2.CascadeClassifier(os.path.join(yol, "haarcascade_eye_tree_eyeglasses.xml"))

    def kapat(self):
        pass

    def olc(self, kare):
        gri = cv2.equalizeHist(cv2.cvtColor(kare, cv2.COLOR_BGR2GRAY))
        yuzler = self.yuz.detectMultiScale(gri, 1.2, 5, minSize=(100, 100))
        if len(yuzler) == 0:
            return False, None, []
        x, y, w, h = max(yuzler, key=lambda r: r[2] * r[3])
        ust = gri[y : y + h // 2, x : x + w]
        gozler = self.goz.detectMultiScale(ust, 1.1, 4, minSize=(w // 10, w // 10))
        noktalar = [(x + gx + gw / 2, y + gy + gh / 2) for gx, gy, gw, gh in gozler]
        # Acik goz bulunduysa 0.3, bulunamadiysa 0.0 -> EAR'a benzer bir olcek
        return True, (0.3 if len(gozler) > 0 else 0.0), noktalar


def algilayici_olustur(zorla_haar=False):
    if not zorla_haar:
        try:
            return MediaPipeAlgilayici()
        except Exception as e:
            print(f"[uyari] MediaPipe kullanilamadi ({e}). Yedek yontem kullaniliyor.")
    return HaarAlgilayici()


# --------------------------------------------------------------------------
# Ana dongu
# --------------------------------------------------------------------------
def yazi(kare, metin, konum, renk=(255, 255, 255), olcek=0.7, kalinlik=2):
    cv2.putText(kare, metin, konum, cv2.FONT_HERSHEY_SIMPLEX, olcek, (0, 0, 0), kalinlik + 3, cv2.LINE_AA)
    cv2.putText(kare, metin, konum, cv2.FONT_HERSHEY_SIMPLEX, olcek, renk, kalinlik, cv2.LINE_AA)


def buton_ciz(kare, metin, x1, y1, x2, y2, renk):
    """Kareye tiklanabilir bir dugme cizer, koordinatlarini dondurur."""
    cv2.rectangle(kare, (x1, y1), (x2, y2), renk, -1)
    cv2.rectangle(kare, (x1, y1), (x2, y2), (255, 255, 255), 2)
    (tw, th), _ = cv2.getTextSize(metin, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
    cv2.putText(kare, metin, (x1 + (x2 - x1 - tw) // 2, y1 + (y2 - y1 + th) // 2),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
    return (x1, y1, x2, y2)


def main():
    ap = argparse.ArgumentParser(description="Gozlerin kapaninca seni uyandiran alarm.")
    ap.add_argument("--sure", type=float, default=3.0, help="Kac saniye goz kapali kalinca alarm calsin (vars. 3)")
    ap.add_argument("--yuz-yok", type=float, default=10, help="Yuz bu kadar saniye gorunmezse de alarm cal (vars. 10, 0 = kapali)")
    ap.add_argument("--uyanik-sure", type=float, default=2.0, help="Alarmin susmasi icin gozlerin kac sn acik kalmali (vars. 2)")
    ap.add_argument("--esik", type=float, default=None, help="Elle goz kapali esigi (EAR, orn. 0.2). Verilmezse otomatik kalibre edilir")
    ap.add_argument("--kamera", type=int, default=0, help="Kamera numarasi (vars. 0)")
    ap.add_argument("--ses", type=str, default=None, help="Alarm icin ses dosyasi (wav/mp3/ogg)")
    ap.add_argument("--ses-seviyesi", type=float, default=1.0, help="En yuksek ses seviyesi 0-1 (vars. 1.0)")
    ap.add_argument("--haar", action="store_true", help="MediaPipe yerine basit OpenCV yontemini kullan")
    ap.add_argument("--pencere-yok", action="store_true", help="Kamera penceresini gosterme (Ctrl+C ile cik)")
    args = ap.parse_args()

    algilayici = algilayici_olustur(args.haar)
    alarm = Alarm(args.ses, max(0.05, min(1.0, args.ses_seviyesi)))

    kamera = cv2.VideoCapture(args.kamera)
    if not kamera.isOpened():
        print("HATA: Kamera acilamadi. Baska bir uygulama kullaniyor olabilir ya da izin verilmemis olabilir.")
        sys.exit(1)

    pencere = "UYAN"
    if not args.pencere_yok:
        cv2.namedWindow(pencere, cv2.WINDOW_NORMAL)

    # Fare ile tiklanan dugmeler: {"kapat": (x1, y1, x2, y2), ...}
    butonlar = {}
    tiklanan = []

    def fare(olay, x, y, *_):
        if olay == cv2.EVENT_LBUTTONDOWN:
            for ad, (x1, y1, x2, y2) in butonlar.items():
                if x1 <= x <= x2 and y1 <= y <= y2:
                    tiklanan.append(ad)

    if not args.pencere_yok:
        cv2.setMouseCallback(pencere, fare)

    def kapat_butonu(kare):
        w = kare.shape[1]
        butonlar["kapat"] = buton_ciz(kare, "KAPAT", w - 130, 10, w - 10, 55, (50, 50, 50))

    # Haar zaten 0 / 0.3 verdigi icin sabit esik yeterli
    esik = args.esik if args.esik is not None else (0.15 if isinstance(algilayici, HaarAlgilayici) else None)
    kalibrasyon = [] if esik is None else None
    kalibrasyon_bas = time.time()

    kapali_bas = None      # gozlerin kapandigi an
    yuz_yok_bas = None     # yuzun kayboldugu an
    acik_bas = None        # alarm calarken gozlerin acildigi an
    ear_gecmis = []        # titremeyi azaltmak icin kisa ortalama
    susturma_bitis = 0

    print(f"Algilama yontemi: {algilayici.ad}")
    print(f"Gozler {args.sure:.0f} sn kapali kalirsa alarm calacak." + (f" Yuz {args.yuz_yok:.0f} sn gorunmezse de." if args.yuz_yok > 0 else ""))
    if kalibrasyon is not None:
        print("Kalibrasyon: ilk 3 saniye kameraya gozlerin ACIK bak...")

    try:
        while True:
            ok, kare = kamera.read()
            if not ok:
                time.sleep(0.05)
                continue
            kare = cv2.flip(kare, 1)
            simdi = time.time()
            yuz_var, ear, noktalar = algilayici.olc(kare)

            if ear is not None:
                ear_gecmis.append(ear)
                ear_gecmis = ear_gecmis[-3:]
                ear = sum(ear_gecmis) / len(ear_gecmis)

            # ---- Kalibrasyon ----
            if kalibrasyon is not None:
                if ear is not None:
                    kalibrasyon.append(ear)
                kalan = 3 - (simdi - kalibrasyon_bas)
                if kalan <= 0 and len(kalibrasyon) >= 10:
                    acik_ort = float(np.median(kalibrasyon))
                    esik = acik_ort * 0.70
                    kalibrasyon = None
                    print(f"Kalibrasyon tamam. Acik goz: {acik_ort:.3f}  esik: {esik:.3f}")
                if not args.pencere_yok:
                    yazi(kare, "Kalibrasyon: gozlerin ACIK kameraya bak", (10, 30), (0, 255, 255))
                    yazi(kare, f"{max(0, kalan):.1f} sn" if yuz_var else "Yuz bulunamadi...", (10, 60), (0, 255, 255))
                    butonlar.clear()
                    kapat_butonu(kare)
                    cv2.imshow(pencere, kare)
                    tus = cv2.waitKey(1) & 0xFF
                    if tus in (ord("q"), 27) or "kapat" in tiklanan:
                        break
                    tiklanan.clear()
                continue

            # ---- Durum takibi ----
            uyku = False
            sebep = ""
            gozler_kapali = yuz_var and ear is not None and ear < esik

            if yuz_var:
                yuz_yok_bas = None
                if gozler_kapali:
                    acik_bas = None
                    kapali_bas = kapali_bas or simdi
                    if simdi - kapali_bas >= args.sure:
                        uyku, sebep = True, "GOZLER KAPALI"
                else:
                    kapali_bas = None
            else:
                kapali_bas = None
                acik_bas = None
                yuz_yok_bas = yuz_yok_bas or simdi
                if args.yuz_yok > 0 and simdi - yuz_yok_bas >= args.yuz_yok:
                    uyku, sebep = True, "YUZ GORUNMUYOR"

            if uyku and simdi >= susturma_bitis:
                if not alarm.aktif:
                    print(time.strftime("[%H:%M:%S]"), "UYAN! ->", sebep)
                    if not args.pencere_yok:
                        try:
                            cv2.setWindowProperty(pencere, cv2.WND_PROP_TOPMOST, 1)
                        except Exception:
                            pass
                alarm.baslat()

            # Alarm caliyorsa: gozler uyanik-sure boyunca acik kalinca sustur
            if alarm.aktif and yuz_var and not gozler_kapali:
                acik_bas = acik_bas or simdi
                if simdi - acik_bas >= args.uyanik_sure:
                    alarm.durdur()
                    acik_bas = None
                    print(time.strftime("[%H:%M:%S]"), "Uyandin, alarm sustu.")

            # ---- Ekran ----
            if not args.pencere_yok:
                for px, py in noktalar:
                    cv2.circle(kare, (int(px), int(py)), 2, (0, 255, 0), -1)
                if alarm.aktif and int(simdi * 4) % 2 == 0:
                    kirmizi = np.zeros_like(kare)
                    kirmizi[:] = (0, 0, 255)
                    kare = cv2.addWeighted(kare, 0.4, kirmizi, 0.6, 0)
                if alarm.aktif:
                    yazi(kare, "UYAN!", (kare.shape[1] // 2 - 110, kare.shape[0] // 2), (255, 255, 255), 2.5, 6)
                    yazi(kare, "Gozlerini acik tut ya da SUSTUR'a bas", (10, kare.shape[0] - 20), (255, 255, 255), 0.6)

                if not yuz_var:
                    durum, renk = "Yuz bulunamadi", (0, 165, 255)
                elif gozler_kapali:
                    durum, renk = f"Gozler kapali: {simdi - kapali_bas:.1f} / {args.sure:.0f} sn", (0, 0, 255)
                else:
                    durum, renk = "Gozler acik", (0, 255, 0)
                yazi(kare, durum, (10, 30), renk)
                if ear is not None:
                    yazi(kare, f"Goz acikligi: {ear:.3f}  esik: {esik:.3f}", (10, 60), (255, 255, 255), 0.55, 1)
                yazi(kare, "q: cikis  bosluk: sustur  c: kalibre", (10, 85), (200, 200, 200), 0.5, 1)

                butonlar.clear()
                kapat_butonu(kare)
                if alarm.aktif:
                    h, w = kare.shape[:2]
                    butonlar["sustur"] = buton_ciz(kare, "SUSTUR", w // 2 - 90, h - 110, w // 2 + 90, h - 55, (0, 140, 0))

                cv2.imshow(pencere, kare)
                tus = cv2.waitKey(1) & 0xFF
                tikla = set(tiklanan)
                tiklanan.clear()
                if tus in (ord("q"), 27) or "kapat" in tikla:
                    break
                if tus == ord(" ") or "sustur" in tikla:
                    alarm.durdur()
                    kapali_bas = None
                    susturma_bitis = simdi + 5  # 5 sn yeniden calma
                if tus == ord("c") and args.esik is None and isinstance(algilayici, MediaPipeAlgilayici):
                    alarm.durdur()
                    kalibrasyon, kalibrasyon_bas = [], time.time()
                try:
                    if cv2.getWindowProperty(pencere, cv2.WND_PROP_VISIBLE) < 1:
                        break
                except Exception:
                    pass
    except KeyboardInterrupt:
        pass
    finally:
        alarm.durdur()
        kamera.release()
        algilayici.kapat()
        try:
            cv2.destroyAllWindows()
        except cv2.error:
            pass
        print("Cikildi.")


if __name__ == "__main__":
    main()
