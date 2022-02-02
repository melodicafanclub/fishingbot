import websocket
from requests import Session
import json
from threading import Thread
from time import sleep, time
from random import uniform
import pytesseract
from PIL import Image, ImageOps, ImageFilter
import shutil
import re
import os
import numpy as np
from scipy.ndimage.filters import gaussian_filter
import cv2
from itertools import cycle


"""USER SETTINGS"""
# Read settings.json Values
f = open('settings.json')
settings = json.load(f)
f.close()


CHANNEL_ID = settings["fishing_channel_id"]  # Channel For Fishing
DISCORD_API_TOKEN = settings["discord_client_token"]  # Account API token
LOWERCASE_DISCORD_NAME = settings["lowercase_username"].lower()
COOLDOWN_TIME = settings["fishing_cooldown"]
TYPE = settings["type"]
MAX_LOSSES = settings["max_losses"]
LOSS_CAP = settings["loss_cap"]

"""BOT SETTINGS"""
DEBUG = False
FISH_ENDPOINT = f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages"
USERAGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36'
PREFIX = "$"
POWER = 3

# Setup Requests Session
session = Session()

# Gamble Cycle
ht = cycle(['h', 't'])
guess = 'h'
games = []


def send_json_request(ws, request):
    ws.send(json.dumps(request))


def receive_json_response(ws):
    response = ws.recv()
    if response:
        return json.loads(response)


def heartbeat(ws, interval):
    print("Heartbeat Started")
    while True:
        heartbeat_json = {
            "op": 1,
            "d": "null"
        }
        send_json_request(ws, heartbeat_json)
        sleep(interval)


def identify(ws):
    payload = {
        "op": 2,
        "d": {
            "token": DISCORD_API_TOKEN,
            "properties": {
                "$os": "windows",
                "$browser": "chrome",
                "$device": "pc"
            }
        }
    }

    send_json_request(ws, payload)


def send_req(payload):
    #  Send Fish Message
    request = session.post(FISH_ENDPOINT, headers={'Authorization': DISCORD_API_TOKEN, 'User-Agent': USERAGENT},
                           json=payload)

    if int(str(request.status_code)) == "429":
        sleep(300)
    # Check if request was valid
    elif int(str(request.status_code)[0]) != 2:
        print("\n\nSEND FROM HERE DOWN:\n\nError: " + str(request.status_code))
        print("\n\n", request.content)
        print("\n\n", "Send This To Xander#5341 to fix it...")

    # Cooldown and prevent rate limiting and add a little randomness
    if TYPE == "fish":
        sleep(uniform(COOLDOWN_TIME, COOLDOWN_TIME + 0.2))
    elif TYPE == "cf":
        sleep(uniform(4, 5))


def fish():
    #  Send Fish Message
    fish_json = {"content": f"{PREFIX}fish", "tts": False}
    send_req(fish_json)


def cf(amount, change):
    #  Send Fish Message
    global guess
    if change:
        guess = next(ht)

    cf_json = {"content": f"{PREFIX}cf {guess} {amount}", "tts": False}
    send_req(cf_json)


def get_balance():
    balance_json = {"content": f"{PREFIX}balance", "tts": False}
    send_req(balance_json)


def solve_captcha(ans):
    regen_json = {f"content": f"{PREFIX}verify {ans}", "tts": False}
    send_req(regen_json)


def get_new_image_captcha():
    regen_json = {f"content": f"{PREFIX}verify regen", "tts": False}
    send_req(regen_json)


def get_image(data):
    url = re.search("(?P<url>https?://[^\s]+)", data).group("url")

    response = session.get(url, stream=True)
    with open('img.png', 'wb') as out_file:
        shutil.copyfileobj(response.raw, out_file)
    del response


def solve_image_captcha():
    image = Image.open("img.png").convert('RGB')
    image = ImageOps.autocontrast(image)

    filename = "img.png".format(os.getpid())
    image.save(filename)

    th1 = 100
    th2 = 140  # threshold after blurring
    sig = 1.5  # the blurring sigma

    black_and_white = image.convert("L")  # converting to black and white
    first_threshold = black_and_white.point(lambda p: p > th1 and 255)
    blur = np.array(first_threshold)  # create an image array
    blurred = gaussian_filter(blur, sigma=sig)
    blurred = Image.fromarray(blurred)
    final = blurred.point(lambda p: p > th2 and 255)
    final = final.filter(ImageFilter.EDGE_ENHANCE_MORE)
    final = final.filter(ImageFilter.SHARPEN)
    final.save("final.png")
    final = cv2.imread("final.png")

    gray = cv2.cvtColor(final, cv2.COLOR_BGR2GRAY)
    gray = cv2.bitwise_not(gray)

    thresh = cv2.threshold(gray, 0, 255,
                           cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]

    coords = np.column_stack(np.where(thresh > 0))
    angle = cv2.minAreaRect(coords)[-1]
    print(angle)

    if angle < -45:
        angle = -(90 + angle)

    else:
        if 45 > angle > 15:
            angle /= 2
        angle = -angle

    if angle < -45:
        angle += 90

    (h, w) = final.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(final, M, (w, h),
                             flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    cv2.imwrite("final.png", rotated)

    text = pytesseract.image_to_string(Image.open('final.png'), lang='eng', config='--psm 6').replace(" ", "")
    print("[INFO] Captcha solving angle: {:.3f}".format(angle), f"Solution: {text}")
    return text


if __name__ == '__main__':
    # Connect to Gateway
    ws = websocket.WebSocket()
    ws.connect("wss://gateway.discord.gg/?v=9&encoding=json")
    event = receive_json_response(ws)
    heartbeat_interval = event['d']["heartbeat_interval"] / 1000  # sleep in seconds

    # Start Heartbeat Thread
    heartbeat_thread = Thread(target=heartbeat, args=(ws, heartbeat_interval,), daemon=True)
    heartbeat_thread.start()

    # Identify
    identify(ws)
    sleep(8)  # Allow Time for bot to identify

    print("STARTING BOT")

    solving_captcha = False
    solving_text = False
    captcha_text = {
        "text": "",
        "checked": []
    }

    # Captcha Vars
    time_since_last_captcha = 0
    num_captcha = 0


    # Fishing Vars
    time_started = time()
    num_fishes = 0

    # CF Vars
    start_balance = 0
    balance = 0
    current_cf_amount = 1
    getting_balance = True
    loss_in_row = 0
    coinflips = 0

    # Event Loop
    while True:
        if not solving_captcha and not solving_text:
            if TYPE == "fish":
                fish()
                num_fishes += 1
                print(f"Fished {num_fishes} times so far. Fishing For {round((((time() - time_started) / 60) / 60), 2)} hours.")
            elif TYPE == "cf":
                if getting_balance:
                    get_balance()
                else:
                    if loss_in_row == MAX_LOSSES - 1:
                        if LOSS_CAP:
                            loss_in_row = 0
                            get_balance()
                            getting_balance = True
                    elif loss_in_row > 3:
                        loss_in_row = 0
                        cf(current_cf_amount, True)
                    else:
                        cf(current_cf_amount, False)

        loops = 0
        while True:
            event = receive_json_response(ws)
            if event:
                op_code = event["op"]
                if op_code == 0:
                    if "author" in event['d'] and "id" in event['d']["author"]:
                        if event['d']["author"]["id"] == "574652751745777665":
                            if event['d']["channel_id"] == str(CHANNEL_ID):
                                if DEBUG:
                                    print(event['d'])
                                if LOWERCASE_DISCORD_NAME in str(event['d']).lower():
                                    # Image Captcha
                                    if "5 times" in str(event['d']):
                                        solving_captcha = True
                                        get_image(event['d']['content'])
                                        captcha_text["text"] = solve_image_captcha()
                                        for char in captcha_text:
                                            captcha_text["checked"].append(False)
                                        solve_captcha(captcha_text["text"])
                                        sleep(COOLDOWN_TIME)
                                        time_since_last_captcha = time()
                                        # Break Out Of Loop
                                        break

                                    elif "**5** times" in str(event['d']).lower():
                                        solving_captcha = True
                                        get_image(event['d']["embeds"][0]["image"]["url"])
                                        captcha_text["text"] = pytesseract.image_to_string(Image.open('img.png'), lang='eng', config='--psm 6').replace(" ", "")
                                        for char in captcha_text:
                                            captcha_text["checked"].append(False)
                                        solve_captcha(captcha_text["text"])
                                        sleep(COOLDOWN_TIME)
                                        time_since_last_captcha = time()
                                        # Break Out Of Loop
                                        break

                                    # AddingCaptcha
                                    elif "please enter the answer to the following problem" in str(event['d']).lower():
                                        solving_text = True
                                        desc = event['d']["embeds"][0]["description"]
                                        desc = desc.rstrip(".")
                                        desc = desc.replace("*", "")

                                        numbers = []
                                        for word in desc.split():
                                            if word.isdigit():
                                                numbers.append(int(word))

                                        result = 0
                                        for i in numbers:
                                            result += i
                                        solve_captcha(result)
                                        sleep(COOLDOWN_TIME)
                                        # Break Out Of Loop
                                        break

                                    # Answer Captcha
                                    elif "please verify with this code" in str(event['d']).lower():
                                        desc = event['d']["content"]
                                        string = desc.split("code: ", 1)[1]
                                        solve_captcha(string)
                                        sleep(COOLDOWN_TIME)

                                    elif "you caught" in str(event['d']).lower():
                                        break

                                    elif "coinflip" in str(event['d']).lower():
                                        if "you win" in str(event['d']).lower():
                                            track_chances = open('ht.txt', 'a')
                                            track_chances.write(guess)
                                            track_chances.close()
                                            loss_in_row = 0
                                            balance += current_cf_amount
                                            print(f"WON CF OF ${int(current_cf_amount)} \tPROFIT: ${int(balance - start_balance)}")
                                            current_cf_amount = int(round(pow(1 / 2, MAX_LOSSES) * balance, 0))
                                        else:
                                            loss_in_row += 1
                                            balance -= current_cf_amount
                                            current_cf_amount = current_cf_amount * POWER

                                            with open("ht.txt", 'a') as track_chances:
                                                if guess == 'h':
                                                    track_chances.write('t')
                                                else:
                                                    track_chances.write('h')

                                        coinflips += 1
                                        if coinflips != 0 and coinflips % 10 == 0:
                                            with open("ht.txt", 'a') as track_chances:
                                                track_chances.write(',')
                                        break

                                    elif "balance for user " in str(event['d']).lower():
                                        if getting_balance:
                                            getting_balance = False
                                            desc = event['d']["embeds"][0]["description"]
                                            desc = desc.rstrip(".")
                                            desc = desc.replace("*", "")
                                            desc = desc.replace(",", "")
                                            desc = desc.replace("$", "")
                                            desc = desc.replace(":", "")

                                            numbers = []
                                            for word in desc.split():
                                                if word.isdigit():
                                                    numbers.append(int(word))

                                            balance = numbers[0]
                                            start_balance = balance
                                            current_cf_amount = int(round(pow(1 / 2, MAX_LOSSES) * balance, 0))
                                            break

                                    elif not solving_captcha and not solving_text and "verify" not in str(event['d']).lower():
                                        break

                                elif "you must wait" in str(event['d']).lower():
                                    #sleep(COOLDOWN_TIME)
                                    break

                                elif "solve the captcha posted above" in str(event['d']).lower():
                                    solving_captcha = True
                                    get_new_image_captcha()
                                    time_since_last_captcha = time()
                                    break

                                elif "you may now continue" in str(event['d']).lower():
                                    print("Solved Captcha")
                                    solving_captcha = False
                                    solving_text = False
                                    captcha_text["text"] = ""
                                    captcha_text["checked"] = []
                                    sleep(COOLDOWN_TIME)
                                    break


                if op_code == 11:
                    pass
                    # print("heartbeat ack")

                if solving_captcha and time() - time_since_last_captcha > 10:
                    text = ""
                    i = 0
                    k = 0
                    print(captcha_text["text"])
                    for character in captcha_text["text"]:
                        if character.isalpha() and captcha_text["checked"][i] is False:
                            character = character.swapcase()
                            captcha_text["checked"][i] = True
                            k += 1
                            break
                        text += character
                        i += 1

                    if k < 1:
                        if num_captcha < 5:
                            get_new_image_captcha()
                            num_captcha += 1
                        else:
                            print("[INFO]: Unable to solve captcha :<")
                            exit()
                    else:
                        solve_captcha(text)
                        time_since_last_captcha = time()
