# coding = utf-8
import pyttsx3
import time
from deep_translator import GoogleTranslator
import random
import translators as ts

def get_translation(text, internet):
    try:
        if internet == "2":
            return translator.translate(text)
        else:
            return ts.translate_text(text, translator="bing", to_language="zh-CHS")  #　"zh-CHS" (中文), "ja" (日语), "en" (英语)
    except Exception as er:
        print(er)
        return "翻译超时，请检查网络"

while True:
    lang_choice = input("请选择朗读语言（j=日语, e=英语）：").strip().lower()
    if lang_choice not in ("j", "e"):
        print("请输入 j 或 e")
        continue
    break

if lang_choice == "j":
    selected_voice = "com.apple.voice.compact.ja-JP.Kyoko"
    path = "wording_jp.txt"
else:
    selected_voice = "Sandy (English (UK)) | com.apple.eloquence.en-GB.Sandy"
    path = "wording_en.txt"

internet_choice = input("你目前是外网环境还是内网环境？默认内网（1=内网，2=外网）")
if internet_choice == "2":
    translator = GoogleTranslator(source="auto", target="zh-CN")

voice_choice = input("你希望调用语音包吗？（y/n 默认y）").strip().lower()

with open(path, "r", encoding="utf-8") as f:
    base = [line.strip() for line in f if line.strip()]
print(f"一共{len(base)}个单词")

while True:
    s = input("你计划抽几个单词：").strip()
    if not s.isdigit():
        print("请输入正整数")
        continue
    n = int(s)
    if n <= 0:
        print("数量必须大于0")
        continue
    if n > len(base):
        print(f"数量不能大于{len(base)}")
        continue
    break

review_list = random.sample(base, n)

for c, word in enumerate(review_list, 1):
    try:
        result = get_translation(text=word, internet=internet_choice)
    except Exception as e:
        result = f"翻译失败：{e}"
    if voice_choice != "n":
        _ = input("按回车播放读音")
        engine = pyttsx3.init()
        engine.setProperty("rate", 90)
        engine.setProperty("volume", 1)
        engine.setProperty("voice", selected_voice)
        engine.say(word)
        engine.runAndWait()
        engine.stop()
        del engine  # 删除变量(引擎对象)
        time.sleep(0.2)
        _ = input("按回车显示原文")
    print(f"{c}: {word}")
    _ = input("按回车显示翻译结果")
    print(f"{c}: {result}")
    if c < len(review_list):
        time.sleep(0.1)
        print("下一轮...")

print("复习结束")
