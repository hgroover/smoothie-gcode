# gcode runner
import io, sys
import socket
import time
import re
import argparse

ADDR="192.168.1.18"
PORT=23
LAST_SENT=""
# Should be ok, error, etc.
LAST_RESPONSE=""
# Optional [Caution: Unlocked] in response to $X
LAST_RESPONSE_MSG=""

# Global values set by get_status()
STATUS=""
MPOS=[0.0, 0.0, 0.0]
WPOS=[0.0, 0.0, 0.0]
FEEDS=[0.0, 0.0]
TIMEOUT_COUNT=0
PASS_TIMEOUT_COUNT=0

# Default response for command timeout is 5 minutes
def timed_cmd(ms, cmd, response_timeout_ms=300000):
    global LAST_SENT
    global LAST_RESPONSE
    global LAST_RESPONSE_MSG
    global TIMEOUT_COUNT
    global PASS_TIMEOUT_COUNT
    started = time.monotonic()
    LAST_SENT=str(cmd, encoding='utf-8')
    prev_timeout = ms.gettimeout()
    ms.settimeout(response_timeout_ms / 1000)
    ms.send(cmd)
    # Minimum turnaround time is 0.5s
    time.sleep(0.5)
    try:
        s = str(ms.recvfrom(4096)[0], encoding='utf-8')
    except:
        TIMEOUT_COUNT = TIMEOUT_COUNT + 1
        PASS_TIMEOUT_COUNT = PASS_TIMEOUT_COUNT + 1
        s = '<timeout>'
    ms.settimeout(prev_timeout)
    elapsed = time.monotonic() - started
    LAST_RESPONSE = s.strip()
    m = re.match('\[([^]]+)\]\W*(\w+)', s)
    if m != None:
        LAST_RESPONSE_MSG = m.group(1)
        LAST_RESPONSE = m.group(2)
    else:
        # Also try error:msg
        m = re.match('(error):(.+)', s)
        if m != None:
            LAST_RESPONSE = m.group(1)
            LAST_RESPONSE_MSG = m.group(2)
        else:
            LAST_RESPONSE_MSG = ""
    # For status query, parse <statusword|MPos|WPos>
    print('sent:', str(cmd, encoding='utf-8').strip(), 'recvd:', LAST_RESPONSE, LAST_RESPONSE_MSG, 'elapsed:', elapsed)
    sys.stdout.flush()

# Get available text with specified timeout in ms
def get_text(ms, timeout_ms):
    prev_timeout = ms.gettimeout()
    ms.settimeout(timeout_ms / 1000)
    try:
        s = str(ms.recvfrom(4096)[0], encoding='utf-8')
    except:
        s = ''
    ms.settimeout(prev_timeout)
    return s

def get_status(ms):
    global STATUS
    global MPOS
    global WPOS
    global FEEDS
    prev_timeout = ms.gettimeout()
    ms.settimeout(1)
    # Smoothie sends both <status|mpos|wpos|feedrates> AND [GC:... in response to ?$G
    ms.send(b'get status\n')
    try:
        s = str(ms.recvfrom(4096)[0], encoding='utf-8')
    except:
        s = 'Timeout'
    ms.settimeout(prev_timeout)
    # <Idle|MPos:10.0000,10.0000,6.0000|WPos:10.0000,10.0000,12.0000|F:1280.0,100.0>
    # If run, we may have L: and S: also
    pat = re.compile('<([^|]+)\|MPos:([^|]+)\|WPos:([^|]+)\|F:([^|>]+)>')
    m = pat.search(s)
    if m is None:
        STATUS = 'Failed to parse {0}'.format(s)
        m = re.search('<(\w+)\|', s)
        if m is None:
            STATUS = 'Failed secondary {0}'.format(s)
        else:
            STATUS = 'Secondary: {0} from {1}'.format(m.group(1), s)
        return STATUS
    STATUS = m.group(1)
    mp_str = m.group(2).split(',')
    wp_str = m.group(3).split(',')
    f_str = m.group(4).split(',')
    MPOS[0] = float(mp_str[0])
    MPOS[1] = float(mp_str[1])
    MPOS[2] = float(mp_str[2])
    WPOS[0] = float(wp_str[0])
    WPOS[1] = float(wp_str[1])
    WPOS[2] = float(wp_str[2])
    FEEDS[0] = float(f_str[0])
    FEEDS[1] = float(f_str[1])
    print('Raw status:', s.strip(), 'parsed:(', STATUS, ') prev timeout:', prev_timeout, 'MP:', MPOS)
    return STATUS

#### Main entry ####

# Parse command line
TARGET_PASSES=2

# Input file
INPUT_FILE='limit-test1.gcode'



try:
    print('Attempting connection via {0} at {1}:{2}'.format('TCP', ADDR, PORT))
    socket.setdefaulttimeout(60)
    msock = socket.socket( socket.AF_INET, socket.SOCK_STREAM )
    
    started = time.monotonic()
    cres = msock.connect( (ADDR, PORT) )
    elapsed = time.monotonic() - started
    print('Connection time:', elapsed)
    # Flush any greeting, usually Smoothie command shell
    time.sleep(2)
    s = get_text(msock, 5000)
    print('Starting text:', s.strip())
    s = get_text(msock, 1000)
    if s != "":
        print('Still starting:', s.strip())
    # Query status - if alarm, send $X to clear and try again
    #timed_cmd(msock, b'get status\n')
    # Smoothie will send <status|mpos|wpos>\n[GC:...] in response to ?$G
    #timed_cmd(msock, b'?$G\n')
    s = get_text(msock, 1000)
    if s != "":
        print('Additional text: {0}'.format(s))
    get_status(msock)
    # If we interrupt a run, we may get an empty status
    if STATUS == '':
        print('Trying status again:')
        get_status(msock)
    print('Status:', STATUS)
    if STATUS == 'Alarm':
        print('Need to clear alarm')
        timed_cmd(msock, b'$X\n')
        if LAST_RESPONSE != 'ok':
            print('Did not get ok:', LAST_RESPONSE)
            sys.exit(1)
    elif STATUS == 'Failed':
        # A previous operation failed. Attempt a wait
        print('A previous operation failed, attempting to clear failure...')
        timed_cmd(msock, b'M400\n')
        get_status(msock)
        print('Response from wait: {0} {1} status: {2}'.format(LAST_RESPONSE, LAST_RESPONSE_MSG, STATUS))
        if STATUS != 'Idle':
            print('Unable to clear failure')
            sys.exit(1)
    elif STATUS != 'Idle':
        print('Status must be idle, got:', STATUS)
        sys.exit(1)
    # Supposed to be time in milliseconds - Smoothie interprets it as seconds
    #timed_cmd(msock, b'G4 P10\n')
    for rpass in range(1, 1 + TARGET_PASSES):
        print('starting pass', rpass, 'of', TARGET_PASSES)
        start_pass = time.monotonic()
        PASS_TIMEOUT_COUNT = 0
        get_status(msock)
        if STATUS != 'Idle':
            print('Non-idle status:', STATUS)
            break
        timed_cmd(msock, b'G17 G90\n')
        timed_cmd(msock, b'G21\n')
        timed_cmd(msock, b'G54\n')
        timed_cmd(msock, b'M42\n')
        # Smoothie switches on for ANY value of S, including 0
        #timed_cmd(msock, b'M3 S0.0\n')
        timed_cmd(msock, b'G0 Z14.0 F60\n')
        if LAST_RESPONSE == 'error':
            sys.exit(1)
        timed_cmd(msock, b'G0 X650.000 Y800.000 F1900\n')
        if LAST_RESPONSE == 'error':
            sys.exit(1)
        # Wait for queued commands to complete for up to 4 minutes
        timed_cmd(msock, b'M400\n', 240000)
        if LAST_RESPONSE == 'error':
            sys.exit(1)
        timed_cmd(msock, b'G1 Z8.8 F160.0\n')
        if LAST_RESPONSE == 'error':
            sys.exit(1)
        timed_cmd(msock, b'G2 X650.0 Y800.0, I-270.0 J0.0 F480.0\n')
        if LAST_RESPONSE == 'error':
            sys.exit(1)
        timed_cmd(msock, b'G0 Z12\n')
        # Also takes 50 seconds sometimes
        timed_cmd(msock, b'M5\n')
        timed_cmd(msock, b'M43\n')
        timed_cmd(msock, b'G0 X10 Y10 F1900\n')
        timed_cmd(msock, b'G17 G90\n')
        # End program. Does nothing on smoothie but takes 50 seconds. Waiting for completion?
        timed_cmd(msock, b'M2\n', 450000)
        elapsed_pass = time.monotonic() - start_pass
        print('pass', rpass, 'total time', elapsed_pass, 'timeouts:', PASS_TIMEOUT_COUNT)
    print('Final pass complete, total timeout count:', TIMEOUT_COUNT)
except OSError as e:
    print('Exception:', e)
    print('last cmd:', LAST_SENT)
    sys.exit(1)

msock.close()
print('Completed')
sys.exit(0)