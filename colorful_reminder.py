from __future__ import print_function

import colorsys
import datetime
import math
import os
import socket
import sys
import time
import pickle

import numpy as np
import oauth2client
import pytz
import unicornhat as lights
from dateutil import parser
from oauth2client import client
from oauth2client import tools
import os.path
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

try:
    import argparse

    flags = argparse.ArgumentParser(parents=[tools.argparser]).parse_args()
except ImportError:
    flags = None

# Google says: If modifying these scopes, delete token.pickle
# On the pi, it's in token.pickle
SCOPES = 'https://www.googleapis.com/auth/calendar.readonly'
CLIENT_SECRET_FILE = 'client_secret.json'
APPLICATION_NAME = 'Colorful Calendar Reminder'
CALENDAR_ID = 'primary'
HASH = '#'
HASHES = '########################################'

# Reminder thresholds
FIRST_THRESHOLD = 60 # minutes, PURPLE lights
SECOND_THRESHOLD = 45 # minutes, GREEN lights
THIRD_THRESHOLD = 30 # minutes, BLUE lights
FOURTH_THRESHOLD = 15 # minutes, ORANGE lights
FIFTH_THRESHOLD = 10 # minutes, PINK lights
SEVENTH_THRESHOLD = 2  # minutes, YELLOW lights before this

# Reboot Options - Added this to enable users to reboot the pi after a certain number of failed retries.
# I noticed that on power loss, the Pi looses connection to the network and takes a reboot after the network
# comes back to fix it.
REBOOT_COUNTER_ENABLED = False
REBOOT_NUM_RETRIES = 10
reboot_counter = 0  # counter variable, tracks retry events.

# COLORS
RED = (255, 0, 0)
ORANGE = (255, 153, 0)
YELLOW = (255, 255, 0)
GREEN = (0, 255, 0)
BLUE = (0, 0, 255)
PURPLE = (153, 51, 255)
PINK = (255, 102, 255)
WHITE = (255, 255, 255)

# constants used in the app to display status
CHECKING_COLOR = BLUE
SUCCESS_COLOR = GREEN
FAILURE_COLOR = RED

# The app needs to track whether there's an existing
# error through the process. If there is, then when checking again for entries
# the app will leave the light red while checking. Setting it to green if
# successful.
has_error = False


def swirl(x, y, step):
    # modified from: https://github.com/pimoroni/unicorn-hat/blob/master/python/examples/demo.py
    x -= 4
    y -= 4

    dist = math.sqrt(pow(x, 2) + pow(y, 2)) / 2.0
    angle = (step / 10.0) + (dist * 1.5)
    s = math.sin(angle)
    c = math.cos(angle)

    xs = x * c - y * s
    ys = x * s + y * c

    r = abs(xs + ys)
    # r = r * 64.0
    r *= 64.0
    r -= 20

    return r, r + (s * 130), r + (c * 130)


def do_swirl(duration):
    # modified from: https://github.com/pimoroni/unicorn-hat/blob/master/python/examples/demo.py
    step = 0
    for i in range(duration):
        for y in range(8):
            for x in range(8):
                r, g, b = swirl(x, y, step)
                r = int(max(0, min(255, r)))
                g = int(max(0, min(255, g)))
                b = int(max(0, min(255, b)))
                lights.set_pixel(x, y, r, g, b)
        step += 1
        lights.show()
        time.sleep(0.01)
    # turn off all lights when you're done
    lights.off()


def set_activity_light(color, increment):
    # used to turn on one LED at a time across the bottom row of lights. The app uses this as an unobtrusive
    # indicator when it connects to Google to check the calendar. Its intended as a subtle reminder that things
    # are still working.
    # it shows GREEN when connecting to Google, then switches to BLUE when its done.
    global current_activity_light
    # turn off (clear) any lights that are on
    lights.off()
    if increment:
        # OK. Which light will we be illuminating?
        if current_activity_light < 1:
            # start over at the beginning when you're at the end of the row
            current_activity_light = 8
        # increment the current light (to the next one)
        current_activity_light -= 1
    # set the pixel color
    lights.set_pixel(current_activity_light, 0, color[0], color[1], color[2])
    # show the pixel
    lights.show()


def set_all(color):
    # light all of the LEDs in a RGB single color.
    for y in range(8):
        for x in range(8):
            lights.set_pixel(x, y, color[0], color[1], color[2])
    lights.show()


def flash_all(flash_count, delay, color):
    # light all of the LEDs in a RGB single color. Repeat 'flash_count' times
    # keep illuminated for 'delay' value
    for index in range(flash_count):
        for y in range(8):
            for x in range(8):
                lights.set_pixel(x, y, color[0], color[1], color[2])
        lights.show()
        time.sleep(delay)
        lights.off()
        time.sleep(delay)


def flash_random(flash_count, delay):
    # Copied from https://github.com/pimoroni/unicorn-hat/blob/master/python/examples/random_blinky.py
    for index in range(flash_count):
        rand_mat = np.random.rand(8, 8)
        for y in range(8):
            for x in range(8):
                h = 0.1 * rand_mat[x, y]
                s = 0.8
                v = rand_mat[x, y]
                rgb = colorsys.hsv_to_rgb(h, s, v)
                r = int(rgb[0] * 255.0)
                g = int(rgb[1] * 255.0)
                b = int(rgb[2] * 255.0)
                lights.set_pixel(x, y, r, g, b)
        lights.show()
        time.sleep(delay)
        lights.off()
        time.sleep(delay)


def get_credentials():
    # taken from https://developers.google.com/google-apps/calendar/quickstart/python
    global credentials
    credentials = None
    # The file token.pickle stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            credentials = pickle.load(token)
    # If there are no (valid) credentials available, let the user log in.
    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                CLIENT_SECRET_FILE, SCOPES)
            credentials = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open('token.pickle', 'wb') as token:
            pickle.dump(credentials, token)
    return credentials


def has_reminder(event):
    # Return true if there's a reminder set for the event
    # First, check to see if there is a default reminder set
    has_default_reminder = event['reminders'].get('useDefault')
    if has_default_reminder:
        # if yes, then we're good
        return True
    else:
        # are there overrides set for reminders?
        overrides = event['reminders'].get('overrides')
        if overrides:
            # OK, then we have a reminder to use
            return True
    # if we got this far, then there must not be a reminder set
    return False


def get_next_event(search_limit):
    global has_error
    global reboot_counter

    # modified from https://developers.google.com/google-apps/calendar/quickstart/python
    # get all of the events on the calendar from now through 10 minutes from now
    print(datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'Getting next event')
    # this 'now' is in a different format (UTC)
    now = datetime.datetime.utcnow()
    then = now + datetime.timedelta(minutes=search_limit)
    # if we don't have an error from the previous attempt, then change the LED color
    # otherwise leave it alone (it should already be red, so it will stay that way).
    if not has_error:
        # turn on a sequential CHECKING_COLOR LED to show that you're requesting data from the Google Calendar API
        set_activity_light(CHECKING_COLOR, True)
    try:
        # ask Google for the calendar entries
        events_result = service.events().list(
            # get all of them between now and 10 minutes from now
            calendarId=CALENDAR_ID,
            timeMin=now.isoformat() + 'Z',
            timeMax=then.isoformat() + 'Z',
            singleEvents=True,
            orderBy='startTime').execute()
        # turn on the SUCCESS_COLOR LED so you'll know data was returned from the Google calendar API
        set_activity_light(SUCCESS_COLOR, False)
        # Get the event list
        event_list = events_result.get('items', [])
        # initialize this here, setting it to true later if we encounter an error
        has_error = False
        # reset the reboot counter, since everything worked so far
        reboot_counter = 0;
        # did we get a return value?
        if not event_list:
            # no? Then no upcoming events at all, so nothing to do right now
            print(datetime.datetime.now(), 'No entries returned')
            return None
        else:
            # what time is it now?
            current_time = pytz.utc.localize(datetime.datetime.utcnow())
            # loop through the events in the list
            for event in event_list:
                # we only care about events that have a start time
                start = event['start'].get('dateTime')
                # return the first event that has a start time
                # so, first, do we have a start time for this event?
                if start:
                    # When does the appointment start?
                    # Convert the string it into a Python dateTime object so we can do math on it
                    event_start = parser.parse(start)
                    # does the event start in the future?
                    if current_time < event_start:
                        # only use events that have a reminder set
                        if has_reminder(event):
                            # no? So we can use it
                            event_summary = event['summary'] if 'summary' in event else 'No Title'
                            print('Found event:', event_summary)
                            print('Event starts:', start)
                            # figure out how soon it starts
                            time_delta = event_start - current_time
                            # Round to the nearest minute and return with the object
                            event['num_minutes'] = time_delta.total_seconds() // 60
                            return event
    except Exception as e:
        # Something went wrong, tell the user (just in case they have a monitor on the Pi)
        print('\nException type:', type(e))
        # not much else we can do here except to skip this attempt and try again later
        print('Error:', sys.exc_info()[0])
        # light up the array with FAILURE_COLOR LEDs to indicate a problem
        flash_all(1, 2, FAILURE_COLOR)
        # now set the current_activity_light to FAILURE_COLOR to indicate an error state
        # with the last reading
        set_activity_light(FAILURE_COLOR, False)
        # we have an error, so make note of it
        has_error = True
        # check to see if reboot is enabled
        if REBOOT_COUNTER_ENABLED:
            # increment the counter
            reboot_counter += 1
            print('Incrementing the reboot counter ({})'.format(reboot_counter))
            # did we reach the reboot threshold?
            if reboot_counter == REBOOT_NUM_RETRIES:
                # Reboot the Pi
                for i in range(1, 10):
                    print('Rebooting in {} seconds'.format(i))
                    time.sleep(1)
                os.system("sudo reboot")

    # if we got this far and haven't returned anything, then there's no appointments in the specified time
    # range, or we had an error, so...
    return None


def main():
    # initialize the lastMinute variable to the current time to start
    last_minute = datetime.datetime.now().minute
    # on startup, just use the previous minute as lastMinute
    if last_minute == 0:
        last_minute = 59
    else:
        last_minute -= 1

    # infinite loop to continuously check Google Calendar for future entries
    while 1:
        # get the current minute
        current_minute = datetime.datetime.now().minute
        # is it the same minute as the last time we checked?
        if current_minute != last_minute:
            # reset last_minute to the current_minute, of course
            last_minute = current_minute
            # we've moved a minute, so we have work to do
            # get the next calendar event (within the specified time limit [in minutes])
            next_event = get_next_event(10)
            # do we get an event?
            if next_event is not None:
                num_minutes = next_event['num_minutes']
                if num_minutes != 1:
                    print('Starts in {} minutes\n'.format(num_minutes))
                else:
                    print('Starts in 1.0 minute\n')
                # is the appointment between 60 and 45 minutes from now?
                if num_minutes >= FIRST_THRESHOLD:
                    # Flash the lights in PURPLE
                    flash_all(1, 0.25, PURPLE)
                    # set the activity light to PURPLE as an indicator
                    set_activity_light(PURPLE, False)
                # is the appointment between 45 and 30 minutes from now?
                elif num_minutes > SECOND_THRESHOLD:
                    # Flash the lights GREEN
                    flash_all(2, 0.25, GREEN)
                    # set the activity light to GREEN as an indicator
                    set_activity_light(GREEN, False)
                # is the appointment between 30 and 15 minutes from now?
                elif num_minutes > THIRD_THRESHOLD:
                    # Flash the lights BLUE
                    flash_all(3, 0.25, BLUE)
                    # set the activity light to BLUE as an indicator
                    set_activity_light(BLUE, False)
                # is the appointment between 15 and 10 minutes from now?
                elif num_minutes > FOURTH_THRESHOLD:
                    # Flash the lights ORANGE
                    flash_all(4, 0.25, ORANGE)
                    # set the activity light to ORANGE as an indicator
                    set_activity_light(ORANGE, False)
                # is the appointment between 10 and 5 minutes from now?
                elif num_minutes > FIFTH_THRESHOLD:
                    # Flash the lights PINK
                    flash_all(5, 0.25, PINK)
                    # set the activity light to PINK as an indicator
                    set_activity_light(PINK, False)
                # is the appointment between 5 and 2 minutes from now?
                elif num_minutes > SIXTH_THRESHOLD:
                    # Flash the lights WHITE
                    flash_all(6, 0.25, WHITE)
                    # set the activity light to WHITE as an indicator
                    set_activity_light(WHITE, False)
                # hmmm, less than 2 minutes, almost time to start!
                else:
                    # swirl the lights. Longer every second closer to start time
                    do_swirl(int((4 - num_minutes) * 100))
                    # set the activity light to SUCCESS_COLOR (green by default)
                    set_activity_light(YELLOW, False)
        # wait a second then check again
        # You can always increase the sleep value below to check less often
        time.sleep(30)


# now tell the user what we're doing...
print('\n')
print(HASHES)
print(HASH, 'Colorful Calendar Reminder          ', HASH)
print(HASH, '      By Amanda Clark               ', HASH)
print(HASHES)

# output whether reboot mode is enabled
if REBOOT_COUNTER_ENABLED:
    print('Reboot enabled ({} retries)'.format(REBOOT_NUM_RETRIES))

# The app flashes a GREEN light in the first row every time it connects to Google to check the calendar.
# The LED increments every time until it gets to the other side then starts over at the beginning again.
# The current_activity_light variable keeps track of which light lit last. At start it's at -1 and goes from there.
current_activity_light = 8

# Set a specific brightness level for the Pimoroni Unicorn HAT, otherwise it's pretty bright.
# Comment out the line below to see what the default looks like.
lights.brightness(0.75)

# flash some random LEDs just for fun...
flash_random(5, 0.1)
# blink all the LEDs GREEN to let the user know the hardware is working
flash_all(1, 1, GREEN)

try:
    # Initialize the Google Calendar API stuff
    print('Initializing the Google Calendar API')
    socket.setdefaulttimeout(60)  # 60 seconds
    credentials = get_credentials()
    service = build('calendar', 'v3', credentials = credentials)

except Exception as e:
    print('\nException type:', type(e))
    # not much else we can do here except to skip this attempt and try again later
    print('Error:', sys.exc_info()[0])
    print('Unable to initialize Google Calendar API')
    # make all the LEDs red
    set_all(FAILURE_COLOR)
    time.sleep(5)
    # then exit, nothing else we can do, right?
    sys.exit(0)

print('Application initialized\n')

# Now see what we're supposed to do next
if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nExiting application\n')
        sys.exit(0)
