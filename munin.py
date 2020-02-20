#!/usr/bin/env python3

__AUTHOR__ = 'Florian Roth'
__VERSION__ = "0.18.1 July 2019"

"""
Install dependencies with:

pip install -r requirements.txt
pip3 install -r requirements.txt
"""

import configparser
import requests
from requests.auth import HTTPBasicAuth
import time
import re
import os
import math
import ast
import signal
import sys
import json
import hashlib
import codecs
import traceback
import argparse
import logging
import gzip
from datetime import datetime
from future.utils import viewitems
from colorama import init, Fore, Back, Style
from lib.helper import generateResultFilename
import cfscrape
# Handle modules that may be difficult to install
# e.g. pymisp has no Debian package, selenium is obsolete

deactivated_features = []
try:
    from pymisp import PyMISP
except ImportError as e:
    print("ERROR: Module 'PyMISP' not found (this feature will be deactivated: MISP queries)")
    deactivated_features.append("pymisp")
try:
    from flask import Flask
    from flask_caching import Cache
    app = Flask(__name__)
    flask_cache = Cache(config={'CACHE_TYPE': 'simple', "CACHE_DEFAULT_TIMEOUT": 15})
    flask_cache.init_app(app)
except ImportError as e:
    traceback.print_exc()
    print("ERROR: Module 'flask' or 'flask_caching' not found (try to fix this with 'pip3 install flask flask_caching'")
    deactivated_features.append("flask")

# CONFIG ##############################################################

# API keys / secrets - please configure them in 'munin.ini'
VT_PUBLIC_API_KEY = '-'
MAL_SHARE_API_KEY = '-'
PAYLOAD_SEC_API_KEY = '-'
PROXY = '-'

VENDORS = ['Microsoft', 'Kaspersky', 'McAfee', 'CrowdStrike', 'TrendMicro',
           'ESET-NOD32', 'Symantec', 'F-Secure', 'Sophos', 'GData']

WAIT_TIME = 16  # Public API allows 4 request per minute, so we wait 15 secs by default

CSV_FIELD_ORDER = ['Lookup Hash', 'Rating', 'Comment', 'Positives', 'Virus', 'File Names', 'First Submitted',
                   'Last Submitted', 'File Type', 'MD5', 'SHA1', 'SHA256', 'Imphash', 'Harmless', 'Revoked',
                   'Expired', 'Trusted', 'Signed', 'Signer', 'Hybrid Analysis Sample', 'MalShare Sample',
                   'VirusBay Sample', 'MISP', 'MISP Events', 'URLhaus', 'AnyRun', 'CAPE', 'VALHALLA', 'User Comments']

CSV_FIELDS = {'Lookup Hash': 'hash',
              'Rating': 'rating',
              'Comment': 'comment',
              'Positives': 'positives',
              'Virus': 'virus',
              'File Names': 'filenames',
              'First Submitted': 'first_submitted',
              'Last Submitted': 'last_submitted',
              'File Type': 'filetype',
              'File Size': 'filesize',
              'MD5': 'md5',
              'SHA1': 'sha1',
              'SHA256': 'sha256',
              'Imphash': 'imphash',
              'Harmless': 'harmless',
              'Revoked': 'revoked',
              'Expired': 'expired',
              'Trusted': 'mssoft',
              'Signed': 'signed',
              'Signer': 'signer',
              'Hybrid Analysis Sample': 'hybrid_available',
              'MalShare Sample': 'malshare_available',
              'VirusBay Sample': 'virusbay_available',
              'MISP': 'misp_available',
              'MISP Events': 'misp_events',
              'URLhaus': 'urlhaus_available',
              'AnyRun': 'anyrun_available',
              'CAPE': 'cape_available',
              'VALHALLA': 'valhalla_matches',
              'Comments': 'comments',
              'User Comments': 'commenter',
              'Reputation': 'reputation',
              'Times Submitted': 'times_submitted',
              'Tags': 'tags',
              }

TAGS = ['HARMLESS', 'SIGNED', 'MSSOFT', 'REVOKED', 'EXPIRED']

# VirusTotal URLs
VT_REPORT_URL = 'https://www.virustotal.com/vtapi/v2/file/report'
VT_COMMENT_API = 'https://www.virustotal.com/ui/files/%s/comments?relationships=item,author'
VT_PERMALINK_URL = 'https://www.virustotal.com/gui/file/%s/detection'
VT_DETAILS = 'https://www.virustotal.com/ui/files/%s'
# MalwareShare URL
MAL_SHARE_URL = 'http://malshare.com/api.php'
# Hybrid Analysis URL
HYBRID_ANALYSIS_URL = 'https://www.hybrid-analysis.com/api/v2/search/hash'
# Hybrid Analysis Download URL
HYBRID_ANALYSIS_DOWNLOAD_URL = 'https://www.hybrid-analysis.com/api/v2/overview/%s/sample'
# Hybrid Analysis Sample URL
URL_HA = 'https://hybrid-analysis.com/sample'
# TotalHash URL
TOTAL_HASH_URL = 'https://totalhash.cymru.com/analysis/'
# VirusBay URL
VIRUSBAY_URL = 'https://beta.virusbay.io/sample/search?q='
# URLhaus
URL_HAUS_URL = "https://urlhaus-api.abuse.ch/v1/payload/"
URL_HAUS_MAX_URLS = 5
# AnyRun
URL_ANYRUN = "https://any.run/report/%s"
# CAPE
URL_CAPE = "https://cape.contextis.com/api/tasks/extendedsearch/"
CAPE_MAX_REPORTS = 5
# Valhalla URL
VALHALLA_URL = "https://valhalla.nextron-systems.com/api/v1/hashinfo"


def processLine(line, debug):
    """
    Process a single line of input
    :param line:
    :param debug:
    :return info:
    :return cooldown_time: remaining cooldown time
    """
    # Measure time for VT cooldown
    start_time = time.time()
    cooldown_time = 0
    # Info dictionary
    info = {"md5": "-", "sha1": "-", "sha256": "-", "vt_queried": False}

    # Remove line break
    line = line.rstrip("\n").rstrip("\r")
    # Skip comments
    if line.startswith("#"):
        return (info, cooldown_time)

    # Get all hashes in line
    # ... and the rest of the line as comment
    hashVal, hashType, comment = fetchHash(line)
    info['hash'] = hashVal
    info[hashType] = hashVal
    info['comment'] = comment

    # If no hash found
    if hashVal == '':
        return (info, cooldown_time)

    # Cache
    cache_result = inCache(hashVal)
    if cache_result:
        info = cache_result
        # But keep the new comment
        info["comment"] = comment
        # New fields - add them to old cache entries
        for key, value in CSV_FIELDS.items():
            if value not in info:
                info[value] = "-"
        # Fix old cached used names
        if ',' in info["commenter"]:
            info["commenter"] = info["commenter"].split(',')
        if debug:
            print("[D] Value found in cache: %s" % cache_result)


    # If found in cache or --nocache set
    if args.nocache or not cache_result:

        # Get Information
        # Virustotal
        vt_info = getVTInfo(hashVal)
        info.update(vt_info)
        # MISP
        misp_info = getMISPInfo(hashVal)
        info.update(misp_info)
        # MalShare
        ms_info = getMalShareInfo(hashVal)
        info.update(ms_info)
        # Hybrid Analysis
        ha_info = getHybridAnalysisInfo(hashVal)
        info.update(ha_info)
        # URLhaus
        uh_info = getURLhaus(info['md5'], info['sha256'])
        info.update(uh_info)
        # AnyRun
        ar_info = getAnyRun(info['sha256'])
        info.update(ar_info)
        # CAPE
        ca_info = getCAPE(info['md5'])
        info.update(ca_info)
        # Valhalla
        valhalla_info = getValhalla(info['sha256'])
        info.update(valhalla_info)

        # TotalHash
        # th_info = {'totalhash_available': False}
        # if 'sha1' in info:
        #     th_info = getTotalHashInfo(info['sha1'])
        # info.update(th_info)

        # VirusBay
        vb_info = getVirusBayInfo(info['md5'])
        info.update(vb_info)

    # Add to hash cache and current batch info list
    if not cache_result:
        cache.append(info)
    # else set vt_queried to False to avoid sleep time
    else:
        info['vt_queried'] = False

    # Wait some time for the next request
    cooldown_time = 0
    if 'vt_queried' in info:  # could be missing on cache values
        if info["vt_queried"]:
            cooldown_time = max(0, WAIT_TIME - int(time.time() - start_time))

    return info, cooldown_time


def processLines(lines, resultFile, nocsv=False, debug=False):
    """
    Process the input file line by line
    """
    # Infos of the current batch
    infos = []

    printHighlighted("[+] Processing %d lines ..." % len(lines))

    # Sorted
    if args.sort:
        lines = sorted(lines)

    for i, line in enumerate(lines):

        # Measure time (used for VT request throttling)
        start_time = time.time()

        # Process the line
        info, cooldown_time = processLine(line, debug)

        # Empty result
        if not info or (info['md5'] == "-" and info['sha1']  == "-" and info['sha256'] == "-"):
            continue

        # Print result
        printResult(info, i, len(lines))

        # Comment on Sample
        if args.comment and info['sha256'] != "-":
            commentVTSample(info['sha256'], "%s %s" % (args.p, info['comment']))

        # Download Samples
        if args.download and 'sha256' in info:
            downloadHybridAnalysisSample(info['sha256'])
        elif args.debug and args.download:
            print("[D] Didn't start download: No sha256 hash found!")

        # Print to CSV
        if not nocsv:
            writeCSV(info, resultFile)

        # Add to infos list
        infos.append(info)

        # Comparison Checks
        peChecks(info, infos)

        # Platform Checks
        platformChecks(info)

        # Wait the remaining colldown time
        time.sleep(cooldown_time)

    return infos


def fetchHash(line):
    """
    Extracts hashes from a line
    :param line:
    :return:
    """
    hashTypes = {32: 'md5', 40: 'sha1', 64: 'sha256'}
    pattern = r'((?<!FIRSTBYTES:\s)|[\b\s]|^)([0-9a-fA-F]{32}|[0-9a-fA-F]{40}|[0-9a-fA-F]{64})(\b|$)'
    hash_search = re.findall(pattern, line)
    # print hash_search
    if len(hash_search) > 0:
        hash = hash_search[0][1]
        rest = ' '.join(re.sub('({0}|;|,|:)'.format(hash), ' ', line).strip().split())
        return hash, hashTypes[len(hash)], rest
    return '', '', ''


def getVTInfo(hash):
    """
    Retrieves many different attributes of a sample from Virustotal via its hash
    :param hash:
    :return:
    """
    # Sample info
    sample_info = {
        'hash': hash,
        "result": "- / -",
        "virus": "-",
        "last_submitted": "-",
        "first_submitted": "-",
        "filenames": "-",
        "filetype": "-",
        "rating": "unknown",
        "positives": 0,
        "res_color": Back.CYAN,
        "imphash": "-",
        "harmless": False,
        "revoked": False,
        "signed": False,
        "expired": False,
        "mssoft": False,
        "vendor_results": {},
        "signer": "",
        "comments": 0,
        "commenter": [],
        "vt_queried": True,
        "tags": [],
    }

    # Prepare VT API request
    parameters = {"resource": hash, "apikey": VT_PUBLIC_API_KEY}
    success = False
    while not success:
        try:
            response_dict_code = requests.get(VT_REPORT_URL, params=parameters, proxies=PROXY)
            response_dict = json.loads(response_dict_code.content.decode("utf-8"))
            success = True
        except Exception as e:
            if args.debug:
                traceback.print_exc()
                # print "Error requesting VT results"
            pass

    sample_info['vt_verbose_msg'] = response_dict.get("verbose_msg")

    if response_dict.get("response_code") > 0:
        # Hashes
        sample_info["md5"] = response_dict.get("md5")
        sample_info["sha1"] = response_dict.get("sha1")
        sample_info["sha256"] = response_dict.get("sha256")
        # AV matches
        sample_info["positives"] = response_dict.get("positives")
        sample_info["total"] = response_dict.get("total")
        sample_info["last_submitted"] = response_dict.get("scan_date")
        # Virus Name
        scans = response_dict.get("scans")
        virus_names = []
        sample_info["vendor_results"] = {}
        for vendor in VENDORS:
            if vendor in scans:
                if scans[vendor]["result"]:
                    virus_names.append("{0}: {1}".format(vendor, scans[vendor]["result"]))
                    sample_info["vendor_results"][vendor] = scans[vendor]["result"]
                else:
                    sample_info["vendor_results"][vendor] = "-"
            else:
                sample_info["vendor_results"][vendor] = "-"

        if len(virus_names) > 0:
            sample_info["virus"] = " / ".join(virus_names)

        # Positives / Total
        sample_info['vt_positives'] = response_dict.get("positives")
        sample_info['vt_total'] = response_dict.get("total")

        # Get more information with permalink -------------------------
        # This is necessary as the VT API does not provide all the needed field values
        if args.debug:
            print("[D] Processing permalink {0}".format(response_dict.get("permalink")))
        info = processPermalink(sample_info["sha256"], args.debug)
        # Now process the retrieved information
        # Other info
        sample_info.update(info)
        # File Names (special handling)
        sample_info["filenames"] = ", ".join(info['filenames']).replace(';', '_')
        sample_info["first_submitted"] = info['firstsubmission']

    return sample_info


def processPermalink(sha256, debug=False):
    """
    Requests the HTML page for the sample and extracts other useful data
    that is not included in the public API
    """
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; MSIE 10.0; Windows NT 6.1; Trident/6.0)',
               'Referrer': 'https://www.virustotal.com/',
               'accept': 'application/json',
               'accpet-Encoding': 'gzip, deflate, br',
               'x-app-hostname': 'https://www.virustotal.com/gui/',
               'x-app-version': '20190717t164815',
               'Cookie': 'euConsent=1; VT_PREFERRED_LANGUAGE=en'}
    info = {'filenames': ['-'], 'firstsubmission': '-', 'harmless': False, 'signed': False, 'revoked': False,
            'expired': False, 'mssoft': False, 'imphash': '-', 'filetype': '-', 'signer': '-',
            'copyright': '-', 'description': '-', 'comments': 0, 'commenter': ['-'], 'tags': [],
            'times_submitted': 0, 'reputation': 0, 'filesize': 0}

    try:
        r_code_details = requests.get(VT_DETAILS % sha256, headers=headers, proxies=PROXY)
        if r_code_details.status_code != 200:
             if 'reCAPTCHA' in r_code_details.content.decode("utf-8"):
                 print("VT_reCAPTCHA issue: Access any report with your browser to unblock your IP, e.g. https://tinyurl.com/vtlup")
        r_details = json.loads(r_code_details.content.decode("utf-8"))

        #print(json.dumps(r_details, indent=4, sort_keys=True))

        # Get file names
        info['filenames'] = r_details['data']['attributes']['names']
        info['filenames'].insert(0, r_details['data']['attributes']['meaningful_name'])
        # Get file type
        info['filetype'] = r_details['data']['attributes']['type_description']
        # Get file size
        info['filesize'] = convertSize(r_details['data']['attributes']['size'])
        # Get tags
        info['tags'] = r_details['data']['attributes']['tags']
        # First submission
        info['firstsubmission'] = datetime.utcfromtimestamp(
            r_details['data']['attributes']['first_submission_date']
        ).strftime('%Y-%m-%d %H:%M:%S')
        # Times submitted
        info['times_submitted'] = r_details['data']['attributes']['times_submitted']
        # Reputation
        info['reputation'] = r_details['data']['attributes']['reputation']

        # Exiftool
        if 'exiftool' in r_details['data']['attributes']:
            if 'LegalCopyright' in r_details['data']['attributes']['exiftool']:
                # Get copyright
                if 'LegalCopyright' in r_details['data']['attributes']['exiftool']:
                    info['copyright'] = r_details['data']['attributes']['exiftool']['LegalCopyright']
                # Get description
                if 'FileDescription' in r_details['data']['attributes']['exiftool']:
                    info['description'] = r_details['data']['attributes']['exiftool']['FileDescription']
        # PE Info
        if 'pe_info' in r_details['data']['attributes']:
            # Get additional information
            info['imphash'] = r_details['data']['attributes']['pe_info']['imphash']
        # PE Signature
        if 'signature_info' in r_details['data']['attributes']:
            # Signer
            if 'signers' in r_details['data']['attributes']['signature_info']:
                info['signer'] = r_details['data']['attributes']['signature_info']['signers']
            # Valid
            if 'verified' in r_details['data']['attributes']['signature_info']:
                if r_details['data']['attributes']['signature_info']['verified'] == "Signed":
                    info['signed'] = True
                if "Revoked" in r_details['data']['attributes']['signature_info']['verified']:
                    info['revoked'] = True
                if "Expired" in r_details['data']['attributes']['signature_info']['verified']:
                    info['expired'] = True

        # Comments
        r_code_comments = requests.get(VT_COMMENT_API % sha256, headers=headers, proxies=PROXY)
        r_comments = json.loads(r_code_comments.content.decode("utf-8"))
        #print(json.dumps(r_comments, indent=4, sort_keys=True))
        info['comments'] = len(r_comments['data'])
        if len(r_comments['data']) > 0:
            info['commenter'] = []
            for com in r_comments['data']:
                info['commenter'].append(com['relationships']['author']['data']['id'])

        # Harmless - TODO: legacy features
        if "Probably harmless!" in r_code_details:
            info['harmless'] = True
        # Microsoft Software
        if "This file belongs to the Microsoft Corporation software catalogue." in r_code_details:
            info['mssoft'] = True
    except Exception as e:
        if args.debug:
            traceback.print_exc()
    finally:
        # Return the info dictionary
        return info


def commentVTSample(resource, comment):
    """
    Posts a comment on a certain sample
    :return:
    """
    params = {
        'apikey': VT_PUBLIC_API_KEY,
        'resource': resource,
        'comment': comment
    }
    response = requests.post('https://www.virustotal.com/vtapi/v2/comments/put', params=params, proxies=PROXY)
    response_json = response.json()
    if response_json['response_code'] != 1:
        print("[E] Error posting comment: %s" % response_json['verbose_msg'])
    else:
        printHighlighted("SUCCESSFULLY COMMENTED")


def getMalShareInfo(hash):
    """
    Retrieves information from MalwareShare https://malshare.com
    :param hash: hash value
    :return info: info object
    """
    info = {'malshare_available': False}
    # Prepare API request
    parameters_query = {"query": hash, "api_key": MAL_SHARE_API_KEY, "action": 'search'}
    parameters_details = {"hash": hash, "api_key": MAL_SHARE_API_KEY, "action": 'details'}
    try:
        response_query = requests.get(MAL_SHARE_URL, params=parameters_query, timeout=3, proxies=PROXY)
        if args.debug:
            print("[D] Querying Malshare: %s" % response_query.request.url)
        #print response_query.content.rstrip('\n')
        # If response is MD5 hash
        if re.match(r'^[a-f0-9]{32}$', response_query.content.decode("utf-8").rstrip('\n')):
            info['malshare_available'] = True
            parameters_details['hash'] = response_query.content.decode("utf-8").rstrip('\n')
            #print parameters_details
            response_details = requests.get(MAL_SHARE_URL, params=parameters_details, proxies=PROXY)
            #print response_details.content
        else:
            info['malshare_available'] = False
            if args.debug:
                print("[D] Malshare response: %s" % response_query.content)
    except Exception as e:
        if args.debug:
            traceback.print_exc()
    return info


def getMISPInfo(hash):
    """
    Retrieves information from a MISP instance
    :param hash: hash value
    :return info: info object
    """
    info = {'misp_available': False, 'misp_events': ''}
    requests.packages.urllib3.disable_warnings()  # I don't care
    # Check if any auth key is set
    key_set = False
    for m in MISP_AUTH_KEYS:
        if m != '' and m != '-':
            key_set = True
    if not key_set or 'pymisp' in deactivated_features:
        return info

    # Loop through MISP instances
    misp_info = []
    misp_events = []
    for c, m_url in enumerate(MISP_URLS, start=0):
        # Get the corresponding auth key
        m_auth_key = MISP_AUTH_KEYS[c]
        if args.debug:
            print("[D] Querying MISP: %s" % m_url)
        try:
            # Preparing API request
            misp = PyMISP(m_url, m_auth_key, args.verifycert, 'json')
            if args.debug:
                print("[D] Query: values=%s" % hash)
            result = misp.search('attributes', values=[hash])
            # Processing the result
            if result['response']:
                events_added = list()
                if args.debug:
                    print(json.dumps(result['response']))
                for r in result['response']["Attribute"]:
                    # Check for duplicates
                    if r['event_id'] in events_added:
                        continue
                    # Try to get info on the events
                    event_info = ""
                    misp_events.append('MISP%d:%s' % (c+1, r['event_id']))
                    e_result = misp.search('events', eventid=r['event_id'])
                    if e_result['response']:
                        event_info = e_result['response'][0]['Event']['info']
                        # too much
                        #if args.debug:
                        #    print(json.dumps(e_result['response']))
                    # Create MISP info object
                    misp_info.append({
                        'misp_nr': c+1,
                        'event_info': event_info,
                        'event_id': r['event_id'],
                        'comment': r['comment'],
                        'url': '%s/events/view/%s' % (m_url, r['event_id'])
                    })
                    events_added.append(r['event_id'])

            else:
                info['misp_available'] = False
        except Exception as e:
            if args.debug:
                traceback.print_exc()

    info['misp_info'] = misp_info
    info['misp_events'] = ", ".join(misp_events)
    if len(misp_events) > 0:
        info['misp_available'] = True

    return info


def getHybridAnalysisInfo(hash):
    """
    Retrieves information from Payload Security's public sandbox https://www.hybrid-analysis.com
    :param hash: hash value
    :return info: info object
    """
    info = {'hybrid_available': False, 'hybrid_score': '-', 'hybrid_date': '-', 'hybrid_compromised': '-'}
    try:
        # Set headers
        headers = {'User-Agent': 'VxStream', 'api-key': PAYLOAD_SEC_API_KEY}
        data = {'hash': hash}
        # Querying Hybrid Analysis
        if args.debug:
            print("[D] Querying Hybrid Analysis")
        response = requests.post(HYBRID_ANALYSIS_URL, headers=headers,
                                timeout=4, proxies=PROXY, data=data)
        res_json = response.json()
        # If response has content
        info['hybrid_available'] = len(res_json) > 0
        if len(res_json) > 0:
            info['hybrid_available'] = True
            if 'threat_score' in res_json[0]:
                info['hybrid_score'] = res_json[0]['threat_score']
            if 'analysis_start_time' in res_json[0]:
                info['hybrid_date'] = res_json[0]['analysis_start_time']
            if 'compromised_hosts' in res_json[0]:
                info['hybrid_compromised'] = res_json[0]['compromised_hosts']
    except ConnectionError as e:
        print("Error while accessing HA: connection failed")
        if args.debug:
            traceback.print_exc()
    except Exception as e:
        print("Error while accessing Hybrid Analysis: %s" % response.content)
        if args.debug:
            traceback.print_exc()
    finally:
        return info


def getValhalla(sha256):
    """
    Retrieves information from Valhalla
    :param sha256: hash value
    :return info: info object
    """
    info = {'valhalla_match': False, 'valhalla_matches': []}
    if "sha256" == "-":
        return info
    if not VALHALLA_API_KEY or VALHALLA_API_KEY == "-":
        return info
    # Ready to go
    if args.debug:
        print("[D] Querying VALHALLA: %s" % sha256)
    try:

        data = {
            "sha256": sha256,
            "apikey": VALHALLA_API_KEY,
        }
        response = requests.post(VALHALLA_URL, data=data, proxies=PROXY)
        if args.debug:
            print("[D] VALHALLA Response: '%s'" % response.json())
        res = response.json()
        if res['status'] == "success":
            info['valhalla_match'] = True
            info['valhalla_matches'] = res['results']
    except Exception as e:
        print("Error while accessing VALHALLA")
        if args.debug:
            traceback.print_exc()
    finally:
        return info


def downloadHybridAnalysisSample(hash):
    """
    Downloads Sample from https://www.hybrid-analysis.com
    :param hash: sha256 hash value
    :return success: bool Download Success
    """
    info = {'hybrid_score': '-', 'hybrid_date': '-', 'hybrid_compromised': '-'}
    try:
        # Prepare request
        preparedURL = HYBRID_ANALYSIS_DOWNLOAD_URL % hash
        # Set user agent string
        headers = {'User-Agent': 'Falcon Sandbox', 'api-key': PAYLOAD_SEC_API_KEY}
        # Prepare Output filename and write the sample
        outfile = os.path.join(args.d, hash)

        # Querying Hybrid Analysis
        if args.debug:
            print("[D] Requesting Downloadsample: %s" % preparedURL)
        response = requests.get(preparedURL, params={'environmentId':'100'}, headers=headers, proxies=PROXY)

        # If the response is a json file
        if response.headers["Content-Type"] == "application/json":
            responsejson = json.loads(response.text)
            if args.debug:
                print("[D] Something went wrong: " +responsejson["message"])
            return False
        # If the content is an octet stream
        elif response.headers["Content-Type"] == "application/gzip":
            plaintextContent = gzip.decompress(response.content)
            f_out = open(outfile, 'wb')
            f_out.write(plaintextContent)
            f_out.close()
            print("[+] Successfully downloaded sample and dropped it to: %s" % outfile)

            # Return successful
            return True
        else:
            if args.debug:
                print("[D] Unexpected content type: " + response.headers["Content-Type"])
            return False
    except ConnectionError as e:
        print("Error while accessing HA: connection failed")
        if args.debug:
            traceback.print_exc()
    except Exception as e:
        print("Error while accessing Hybrid Analysis: %s" % response.content)
        if args.debug:
            traceback.print_exc()
    finally:
        return False


def getTotalHashInfo(sha1):
    """
    Retrieves information from Totalhash https://totalhash.cymru.com
    :param hash: hash value
    :return info: info object
    """
    info = {'totalhash_available': False}
    try:
        # Prepare request
        preparedURL = "%s?%s" % (TOTAL_HASH_URL, sha1)
        # Set user agent string
        # headers = {'User-Agent': ''}
        # Querying Hybrid Analysis
        if args.debug:
            print("[D] Querying Totalhash: %s" % preparedURL)
        response = requests.get(preparedURL, proxies=PROXY)
        # print "Respone: '%s'" % response.content
        if response.content and \
                        '0 of 0 results' not in response.content and \
                        'Sorry something went wrong' not in response.content:
            info['totalhash_available'] = True
    except ConnectionError as e:
        print("Error while accessing Total Hash: connection failed")
        if args.debug:
            traceback.print_exc()
    except Exception as e:
        print("Error while accessing Totalhash: %s" % response.content)
        if args.debug:
            traceback.print_exc()
    return info


def getURLhaus(md5, sha256):
    """
    Retrieves information from URLhaus https://urlhaus-api.abuse.ch/#download-sample
    :param md5: hash value
    :param sha256: hash value
    :return info: info object
    """
    info = {'urlhaus_available': False}
    if 'md5' == "-" and 'sha256' == "-":
        return info
    try:
        if sha256:
            data = {"sha256_hash": sha256}
        else:
            data = {"md5_hash": md5}
        response = requests.post(URL_HAUS_URL, data=data, timeout=3, proxies=PROXY)
        # print("Respone: '%s'" % response.json())
        res = response.json()
        if res['query_status'] == "ok" and res['md5_hash']:
            info['urlhaus_available'] = True
            info['urlhaus_type'] = res['file_type']
            info['urlhaus_url_count'] = res['url_count']
            info['urlhaus_first'] = res['firstseen']
            info['urlhaus_last'] = res['lastseen']
            info['urlhaus_download'] = res['urlhaus_download']
            info['urlhaus_urls'] = res['urls']
    except Exception as e:
        print("Error while accessing URLhaus")
        if args.debug:
            traceback.print_exc()
    return info


def getCAPE(md5):
    """
    Retrieves information from CAPE
    :param md5: hash value
    :return info: info object
    """
    info = {'cape_available': False}
    if md5 == "-":
        return info
    try:
        data = {"option": "md5", "argument": md5}
        response = requests.post(URL_CAPE, data=data, timeout=3, proxies=PROXY)
        # print("Response: '%s'" % response.json())
        res = response.json()
        if not res['error'] and len(res['data']) > 0:
            info['cape_available'] = True
            info['cape_reports'] = res['data']
    except Exception as e:
        if args.debug:
            print("Error while accessing CAPE")
            traceback.print_exc()
    return info


def getAnyRun(sha256):
    """
    Retrieves information from AnyRun Service
    :param sha256: hash value
    :return info: info object
    """
    info = {'anyrun_available': False}
    if sha256 == "-":
        return info
    try:
        #global PROXY
        
        if args.debug:
            print("[D] Querying Anyrun")
        cfscraper = cfscrape.create_scraper()
        response = cfscraper.get(URL_ANYRUN % sha256, proxies=PROXY)
       

        if args.debug:
            print("[D] Anyrun Response Code: %s" %response.status_code)

        if response.status_code == 200:
            info['anyrun_available'] = True
    except ConnectionError as e:
        print("Error while accessing AnyRun: connection failed")
        if args.debug:
            traceback.print_exc()
    except Exception as e:
        print("Error while accessing AnyRun")
        if args.debug:
            traceback.print_exc()
    return info


def getVirusBayInfo(hash):
    """
    Retrieves information from VirusBay https://beta.virusbay.io/
    :param hash: hash value
    :return info: info object
    """
    info = {'virusbay_available': False}
    if hash == "-":
        return info
    try:
        # Prepare request
        preparedURL = "%s%s" % (VIRUSBAY_URL, hash)
        if args.debug:
            print("[D] Querying Virusbay: %s" % preparedURL)
        response = requests.get(preparedURL, proxies=PROXY).json()
        # If response has the correct content
        info['virusbay_available'] = False
        #print(response)
        tags = []
        if response['search'] != []:
            info['virusbay_available'] = True
            for tag in response['search'][0]['tags']:
                tags.append(tag['name'])
            info['vb_tags'] = tags
            info['vb_link'] = "https://beta.virusbay.io/sample/browse/%s" % response['search'][0]['md5']
    except Exception as e:
        if args.debug:
            print("Error while accessing VirusBay")
            traceback.print_exc()
    return info


def peChecks(info, infos):
    """
    Check for duplicate imphashes
    :param info:
    :param infos:
    :return:
    """
    # Some static values
    SIGNER_WHITELIST = ["Microsoft Windows", "Microsoft Corporation"]
    # Imphash check
    imphash_count = 0
    for i in infos:
        if 'imphash' in i and 'imphash' in info:
            if i['imphash'] != "-" and i['imphash'] == info['imphash']:
                imphash_count += 1
    if imphash_count > 1:
        printHighlighted("[!] Imphash - appeared %d times in this batch %s" %
                         (imphash_count, info['imphash']))
    # Signed Appeared multiple times
    try:
        signer_count = 0
        for s in infos:
            if 'signer' in s and 'signer' in info:
                if s['signer'] != "-" and s['signer'] and s['signer'] == info['signer'] and \
                        not any(s in info['signer'] for s in SIGNER_WHITELIST):
                    signer_count += 1
        if signer_count > 1:
            printHighlighted("[!] Signer - appeared %d times in this batch %s" %
                             (signer_count, info['signer'].encode('raw-unicode-escape')))
    except KeyError as e:
        if args.debug:
            traceback.print_exc()


def platformChecks(info):
    """
    Performs certain comparison checks on the given info object compared to past
    evaluations from the current batch and cache
    :param info:
    :return:
    """
    try:
        # MISP results
        if 'misp_available' in info:
            if info['misp_available']:
                for e in info['misp_info']:
                    printHighlighted("[!] MISP event found EVENT_ID: {0} EVENT_INFO: {1} URL: {2}".format(
                        e['event_id'], e['event_info'], e['url'])
                    )
    except KeyError as e:
        if args.debug:
            traceback.print_exc()
    try:
        # Malware Share availability
        if 'malshare_available' in info:
            if info['malshare_available']:
                printHighlighted("[!] Sample is available on malshare.com")
    except KeyError as e:
        if args.debug:
            traceback.print_exc()
    try:
        # Hybrid Analysis availability
        if 'hybrid_available' in info:
            if info['hybrid_available']:
                printHighlighted("[!] Sample is on hybrid-analysis.com SCORE: {0} URL: {1}/{2}".format(
                    info["hybrid_score"], URL_HA, info['sha256']))
    except KeyError as e:
        if args.debug:
            traceback.print_exc()
    try:
        # URLhaus availability
        if 'urlhaus_available' in info:
            if info['urlhaus_available']:
                printHighlighted("[!] Sample on URLHaus URL: %s" % info['urlhaus_download'])
                printHighlighted("[!] URLHaus info TYPE: %s FIRST_SEEN: %s LAST_SEEN: %s URL_COUNT: %s" % (
                    info['urlhaus_type'],
                    info['urlhaus_first'],
                    info['urlhaus_last'],
                    info['urlhaus_url_count']
                ))
                c = 0
                for url in info['urlhaus_urls']:
                    printHighlighted("[!] URLHaus STATUS: %s URL: %s" % (url['url_status'], url['url']))
                    c += 1
                    if c > URL_HAUS_MAX_URLS:
                        break
    except KeyError as e:
        if args.debug:
            traceback.print_exc()
    try:
        # AnyRun availability
        if 'anyrun_available' in info:
            if info['anyrun_available']:
                printHighlighted("[!] Sample on ANY.RUN URL: %s" % (URL_ANYRUN % info['sha256']))
    except KeyError as e:
        if args.debug:
            traceback.print_exc()
    try:
        # CAPE availability
        if 'cape_available' in info:
            if info['cape_available']:
                c = 0
                for r in info['cape_reports']:
                    printHighlighted("[!] Sample on CAPE sandbox URL: https://cape.contextis.com/analysis/%s/" % r)
                    c += 1
                    if c > CAPE_MAX_REPORTS:
                        break
    except KeyError as e:
        if args.debug:
            traceback.print_exc()
    # # Totalhash availability
    # if info['totalhash_available']:
    #     printHighlighted("[!] Sample is available on https://totalhash.cymru.com")
    try:
        # VirusBay availability
        if info['virusbay_available']:
            printHighlighted("[!] Sample is on VirusBay "
                             "URL: %s TAGS: %s" % (info['vb_link'], ", ".join(info['vb_tags'])))
    except KeyError as e:
        if args.debug:
            traceback.print_exc()
    try:
        # Valhalla availability
        if info['valhalla_match']:
            for m in info['valhalla_matches']:
                # Public Rule or Nextron Commercial Feed
                feed = "commercial feed only"
                if 'DEMO' in m['tags']:
                    feed = "public rule LINK: https://github.com/Neo23x0/signature-base/search?q=%s" % m['rulename']
                printHighlighted("[!] VALHALLA YARA rule match "
                                 "RULE: %s TYPE: %s AV: %s / %s TS: %s" %
                                 (m['rulename'], feed, m['positives'], m['total'], m['timestamp']))
    except KeyError as e:
        if args.debug:
            traceback.print_exc()


def printResult(info, count, total):
    """
    prints the result block
    :param info: all collected info
    :param count: counter (number of samples checked)
    :param total: total number of lines to check
    :return:
    """
    # Rating and Color
    info["rating"] = "unknown"
    info["res_color"] = Back.CYAN

    # If VT returned results
    if "vt_total" in info:
        info["rating"] = "clean"
        info["res_color"] = Back.GREEN
        if info["vt_positives"] > 0:
            info["rating"] = "suspicious"
            info["res_color"] = Back.YELLOW
        if info["vt_positives"] > 10:
            info["rating"] = "malicious"
            info["res_color"] = Back.RED

    # Head line
    printSeparator(count, total, info['res_color'], info["rating"])
    printHighlighted("HASH: {0} COMMENT: {1}".format(info["hash"], info['comment']))

    # More VT info
    if "vt_total" in info:
        # Result
        info["result"] = "%s / %s" % (info["vt_positives"], info["vt_total"])
        if info["virus"] != "-":
            printHighlighted("VIRUS: {0}".format(info["virus"]))
        printHighlighted("TYPE: {1} SIZE: {2} FILENAMES: {0}".format(removeNonAsciiDrop(info["filenames"]),
                                                                     info['filetype'],
                                                                     info['filesize']))

        # Tags to show
        tags = ""
        if isinstance(info['tags'], list):
            tags = " ".join(map(lambda x: x.upper(), info['tags']))
        # Extra Info
        printPeInfo(info)
        printHighlighted("FIRST: {0} LAST: {1} SUBMISSIONS: {5} REPUTATION: {6}\nCOMMENTS: {2} USERS: {3} TAGS: {4}".format(
            info["first_submitted"],
            info["last_submitted"],
            info["comments"],
            ', '.join(info["commenter"]),
            tags,
            info["times_submitted"],
            info["reputation"]
        ), tag_color=True)

    else:
        if args.debug:
            printHighlighted("VERBOSE_MESSAGE: %s" % info['vt_verbose_msg'])

    # Print the highlighted result line
    printHighlighted("RESULT: %s" % (info["result"]), hl_color=info["res_color"])


def printHighlighted(line, hl_color=Back.WHITE, tag_color=False):
    """
    Print a highlighted line
    """
    if tag_color:
        # Tags
        colorer = re.compile('(HARMLESS|SIGNED|MS_SOFTWARE_CATALOGUE|MSSOFT|SUCCESSFULLY\sCOMMENTED)', re.VERBOSE)
        line = colorer.sub(Fore.BLACK + Back.GREEN + r'\1' + Style.RESET_ALL + '', line)
        colorer = re.compile('(REVOKED|EXPLOIT|CVE-[0-9\-]+|OBFUSCATED|RUN\-FILE)', re.VERBOSE)
        line = colorer.sub(Fore.BLACK + Back.RED + r'\1' + Style.RESET_ALL + '', line)
        colorer = re.compile('(EXPIRED|VIA\-TOR|OLE\-EMBEDDED|RTF|ATTACHMENT|ASPACK|UPX|AUTO\-OPEN|MACROS)', re.VERBOSE)
        line = colorer.sub(Fore.BLACK + Back.YELLOW + r'\1' + Style.RESET_ALL + '', line)
    # Extras
    colorer = re.compile('(\[!\])', re.VERBOSE)
    line = colorer.sub(Fore.BLACK + Back.LIGHTMAGENTA_EX + r'\1' + Style.RESET_ALL + '', line)
    # Add line breaks
    colorer = re.compile('(ORIGNAME:)', re.VERBOSE)
    line = colorer.sub(r'\n\1', line)
    # Standard
    colorer = re.compile('([A-Z_]{2,}:)\s', re.VERBOSE)
    line = colorer.sub(Fore.BLACK + hl_color + r'\1' + Style.RESET_ALL + ' ', line)
    print(line)


def printSeparator(count, total, color, rating):
    """
    Print a separator line status infos
    :param count:
    :param total:
    :return:
    """
    print(Fore.BLACK + color)
    print(" {0} / {1} > {2}".format(count+1, total, rating.title()).ljust(80) + Style.RESET_ALL)


def printKeyLine(line):
    """
    Print a given string as a separator line
    :param line:
    :return:
    """
    print(Fore.BLACK + Back.WHITE)
    print("{0}".format(line).ljust(80) + Style.RESET_ALL)
    print("")


def printPeInfo(sample_info):
    """
    Prints PE information in a clever form
    :param peInfo:
    :return:
    """
    peInfo = [u'origname', u'description', u'copyright', u'signer']
    outString = []
    for k, v in viewitems(sample_info):
        if k in peInfo:
            if v is not '-':
                outString.append("{0}: {1}".format(k.upper(), removeNonAsciiDrop(v)))
    if " ".join(outString):
        printHighlighted(" ".join(outString))


def saveCache(cache, fileName):
    """
    Saves the cache database as pickle dump to a file
    :param cache:
    :param fileName:
    :return:
    """
    with open(fileName, 'w') as fh:
        fh.write(json.dumps(cache))


def loadCache(fileName):
    """
    Load cache database as json dump from file
    :param fileName:
    :return:
    """
    try:
        with open(fileName, 'r') as fh:
            return json.load(fh), True
    except Exception as e:
        # traceback.print_exc()
        return [], False


def removeNonAsciiDrop(string):
    nonascii = "error"
    # print "CON: ", string
    try:
        # Generate a new string without disturbing characters and allow new lines
        # Python 2 method
        try:
            nonascii = "".join(i for i in string if (ord(i) < 127 and ord(i) > 31) or ord(i) == 10 or ord(i) == 13)
        except Exception as e:
            # Python 3 fallback
            return string
    except Exception as e:
        traceback.print_exc()
        pass
    return nonascii


def inCache(hashVal):
    """
    Check if a sample with a certain hash has already been checked and return the info if true
    :param hashVal: hash value used as reference
    :return: cache element or None
    """
    if not hashVal:
        return None
    for c in cache:
        if c['hash'] == hashVal or c['md5'] == hashVal or c['sha1'] == hashVal or c['sha256'] == hashVal:
            return c
    return None


def knownValue(infos, key, value):
    """
    Checks if a value of a given key appears in a previously checked info and
    returns the info if the value matches
    :param infos:
    :param key:
    :param value:
    :return: info
    """
    for i in infos:
        if i[key] == value:
            return i
    return None


def writeCSV(info, resultFile):
    """
    Write info line to CSV
    :param info:
    :return:
    """
    try:
        with codecs.open(resultFile, 'a', encoding='utf8') as fh_results:
            # Print every field from the field list to the output file
            for field_pretty in CSV_FIELD_ORDER:
                field = CSV_FIELDS[field_pretty]
                try:
                    field = info[field]
                except KeyError as e:
                    field = "False"
                try:
                    field = str(field).replace(r'"', r'\"').replace("\n", " ")
                except AttributeError as e:
                    if args.debug:
                        traceback.print_exc()
                    pass
                fh_results.write("%s;" % field)
            # Append vendor scan results
            for vendor in VENDORS:
                if vendor in info['vendor_results']:
                    fh_results.write("%s;" % info['vendor_results'][vendor])
                else:
                    fh_results.write("-;")
            fh_results.write('\n')
    except:
        if args.debug:
            traceback.print_exc()
        return False
    return True


def writeCSVHeader(resultFile):
    """
    Writes a CSV header line into the results file
    :param resultFile:
    :return:
    """
    try:
        with open(resultFile, 'w') as fh_results:
            fh_results.write("%s;" % ";".join(CSV_FIELD_ORDER))
            fh_results.write("%s;\n" % ";".join(VENDORS))
    except Exception as e:
        print("[E] Cannot write export file {0}".format(resultFile))


def getFileData(filePath):
    """
    Get the content of a given file
    :param filePath:
    :return fileData:
    """
    fileData = ""
    try:
        # Read file complete
        with open(filePath, 'rb') as f:
            fileData = f.read()
    except Exception as e:
        traceback.print_exc()
    finally:
        return fileData


def convertSize(size_bytes):
    """
    Converts number of bytes to a readable form
    Source: https://stackoverflow.com/questions/5194057/better-way-to-convert-file-sizes-in-python
    :param size_bytes:
    :return:
    """
    if size_bytes == 0:
       return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return "%s %s" % (s, size_name[i])


def generateHashes(fileData):
    """
    Generates hashes for a given blob of data
    :param filedata:
    :return hashes:
    """
    hashes = {'md5': '', 'sha1': '', 'sha256': ''}
    try:
        md5 = hashlib.md5()
        sha1 = hashlib.sha1()
        sha256 = hashlib.sha256()
        md5.update(fileData)
        sha1.update(fileData)
        sha256.update(fileData)
        hashes = {'md5': md5.hexdigest(), 'sha1': sha1.hexdigest(), 'sha256': sha256.hexdigest()}
    except Exception as e:
        traceback.print_exc()
    finally:
        return hashes


@app.route('/<string>')
def lookup(string):
    # Is cached
    is_cached = False
    hashVal, hashType, comment = fetchHash(string)
    if inCache(hashVal):
        is_cached = True
    # Still in VT cooldown
    if flask_cache.get('vt-cooldown') and not is_cached:
        return json.dumps({'status': 'VT cooldown active'}), 429
    # Process the input
    info, cooldown_time = processLine(string, args.debug)
    # If VT has been queried set a cooldown period
    if info['vt_queried']:
        flask_cache.set('vt-cooldown', True, timeout=cooldown_time)
    return json.dumps(info)


def signal_handler(signal, frame):
    if not args.nocache:
        print("\n[+] Saving {0} cache entries to file {1}".format(len(cache), args.c))
        saveCache(cache, args.c)
    sys.exit(0)


if __name__ == '__main__':

    init(autoreset=False)

    print(Style.RESET_ALL)
    print(Fore.BLACK + Back.WHITE)
    print("   _________   _    _   ______  _____  ______          ".ljust(80))
    print("  | | | | | \ | |  | | | |  \ \  | |  | |  \ \     /.) ".ljust(80))
    print("  | | | | | | | |  | | | |  | |  | |  | |  | |    /)\| ".ljust(80))
    print("  |_| |_| |_| \_|__|_| |_|  |_| _|_|_ |_|  |_|   // /  ".ljust(80))
    print("                                                /'\" \"  ".ljust(80))
    print(" ".ljust(80))
    print("  Online Hash Checker for Virustotal and Other Services".ljust(80))
    print(("  " + __AUTHOR__ + " - " + __VERSION__ + "").ljust(80))
    print(" ".ljust(80) + Style.RESET_ALL)
    print(Style.RESET_ALL + " ")

    parser = argparse.ArgumentParser(description='Online Hash Checker')
    parser.add_argument('-f', help='File to process (hash line by line OR csv with hash in each line - auto-detects '
                                   'position and comment)', metavar='path', default='')
    parser.add_argument('-c', help='Name of the cache database file (default: vt-hash-db.pkl)', metavar='cache-db',
                        default='vt-hash-db.json')
    parser.add_argument('-i', help='Name of the ini file that holds the API keys', metavar='ini-file',
                        default=os.path.dirname(os.path.abspath(__file__)) + '/munin.ini')
    parser.add_argument('-s', help='Folder with samples to process', metavar='sample-folder',
                        default='')
    parser.add_argument('--comment', action='store_true', help='Posts a comment for the analysed hash which contains '
                                                               'the comment from the log line', default=False)
    parser.add_argument('-p', help='Virustotal comment prefix', metavar='vt-comment-prefix',
                        default='Munin Analyzer Run:\n')

    parser.add_argument('--download', action='store_true', help='Enables Sample Download from Hybrid Analysis. SHA256 of sample needed.', default=False)
    parser.add_argument('-d', help='Output Path for Sample Download from Hybrid Analysis. Folder must exist', metavar='download_path',default='./')

    parser.add_argument('--nocache', action='store_true', help='Do not use cache database file', default=False)
    parser.add_argument('--nocsv', action='store_true', help='Do not write a CSV with the results', default=False)
    parser.add_argument('--verifycert', action='store_true', help='Verify SSL/TLS certificates', default=False)
    parser.add_argument('--sort', action='store_true', help='Sort the input lines', default=False)
    parser.add_argument('--web', action='store_true', help='Run Munin as web service', default=False)
    parser.add_argument('-w', help='Web service port', metavar='port', default=5000)
    parser.add_argument('--cli', action='store_true', help='Run Munin in command line interface mode', default=False)
    parser.add_argument('--debug', action='store_true', default=False, help='Debug output')
    
    args = parser.parse_args()

    # PyMISP error handling > into Nirvana
    logger = logging.getLogger("pymisp")
    logger.setLevel(logging.CRITICAL)
    if args.debug:
        logger.setLevel(logging.DEBUG)

    # Read the config file
    config = configparser.ConfigParser()
    try:
        config.read(args.i)
        VT_PUBLIC_API_KEY = config['DEFAULT']['VT_PUBLIC_API_KEY']
        MAL_SHARE_API_KEY = config['DEFAULT']['MAL_SHARE_API_KEY']
        PAYLOAD_SEC_API_KEY = config['DEFAULT']['PAYLOAD_SEC_API_KEY']
        VALHALLA_API_KEY = config['DEFAULT']['VALHALLA_API_KEY']
        try:
            PROXY = config['DEFAULT']['PROXY']
        except KeyError as e:
            print("[E] Your config misses the PROXY field - check the new munin.ini template and add it to your "
                  "config to avoid this error.")
            PROXY = "-"

        # MISP config
        fall_back = False
        try:
            MISP_URLS = ast.literal_eval(config.get('MISP', 'MISP_URLS'))
            MISP_AUTH_KEYS = ast.literal_eval(config.get('MISP','MISP_AUTH_KEYS'))
        except Exception as e:
            if args.debug:
                traceback.print_exc()
            print("[E] Since munin v0.13.0 you're able to define multiple MISP instances in config. The new .ini "
                  "expects the MISP config lines to contain lists (see munin.ini). Falling back to old config format.")
            fall_back = True

        # Fallback to old config
        if fall_back:
            MISP_URLS = list([config.get('MISP', 'MISP_URL')])
            MISP_AUTH_KEYS = list([config.get('MISP', 'MISP_API_KEY')])

    except Exception as e:
        traceback.print_exc()
        print("[E] Config file '%s' not found or missing field - check the template munin.ini if fields have "
              "changed" % args.i)

    # Check API Key
    if VT_PUBLIC_API_KEY == '' or not re.match(r'[a-fA-F0-9]{64}', VT_PUBLIC_API_KEY):
        print("[E] No Virustotal API Key set or wrong format")
        print("    Include your API key in a custom config file and use munin.ini as a template\n")
        print("    More info:")
        print("    https://github.com/Neo23x0/munin#get-the-api-keys-used-by-munin\n")
        sys.exit(1)

    # Create valid ProxyDict 
    if PROXY != "-":
        proxy_string = PROXY
        PROXY = {'http': proxy_string, 'https': proxy_string}
    # No proxy if nothing is set in .ini
    else:
        PROXY = {}


    # Trying to load cache from JSON dump
    cache = []
    if not args.nocache:
        cache, success = loadCache(args.c)
        if success:
            print("[+] {0} cache entries read from cache database: {1}".format(len(cache), args.c))
        else:
            print("[-] No cache database found")
            print("[+] Analyzed hashes will be written to cache database: {0}".format(args.c))
        print("[+] You can interrupt the process by pressing CTRL+C without losing the already gathered information")

    # Now add a signal handler so that no results get lost
    signal.signal(signal.SIGINT, signal_handler)

    # CLI ---------------------------------------------------------------------
    # Check input file
    if args.cli:
        alreadyExists, resultFile = generateResultFilename(args.f)
        print("")
        print("Command Line Interface Mode")
        print("")
        print("Paste your content into the command line window and then press CTRL+D to process the pasted content.")
        print("Make sure that your last content line has a line break at its end (press ENTER before CTRL+D).")
        print("[+] Results will be written to: %s" % resultFile)
        print("Exit with CTRL+C")
        while True:
            printKeyLine("PASTE CONTENT & PROCESS WITH CTRL+D:")
            contents = []
            while True:
                try:
                    line = input()
                except EOFError:
                    break
                contents.append(line)
            # Process the input
            printKeyLine("END OF CONTENT")
            infos = processLines(contents, resultFile, nocsv=args.nocsv, debug=args.debug)
            if len(infos) == 0:
                printHighlighted("[!] Content needs at least 1 hash value in it")

    # Web Service -------------------------------------------------------------
    if args.web:
        if 'flask' in deactivated_features:
            print("[E] Flask module has not been loaded. Try to install it with 'pip3 install flask' before using "
                  "this feature")
            sys.exit(1)
        print("")
        print("Web Service Mode")
        print("")
        alreadyExists, resultFile = generateResultFilename(args.f)
        print("Send you requests to http://server:%d/value")
        printKeyLine("STARTING FLASK")
        app.run(port=int(args.w))


    # DEFAULT -----------------------------------------------------------------
    # Open input file
    if args.f:
        # Generate a result file name
        alreadyExists, resultFile = generateResultFilename(args.f)
        try:
            with open(args.f, 'r') as fh:
                lines = fh.readlines()
        except Exception as e:
            print("[E] Cannot read input file")
            sys.exit(1)
    if args.s:
        # Generate a result file name
        pathComps = args.s.split(os.sep)
        if pathComps[-1] == "":
            del pathComps[-1]
        alreadyExists, resultFile = generateResultFilename(pathComps[-1])
        # Empty lines container
        lines = []
        for root, directories, files in os.walk(args.s, followlinks=False):
            for filename in files:
                try:
                    filePath = os.path.join(root, filename)
                    print("[ ] Processing %s ..." % filePath)
                    fileData = getFileData(filePath)
                    hashes = generateHashes(fileData)
                    # Add the results as a line for processing
                    lines.append("{0} {1}".format(hashes["sha256"], filePath))
                except Exception as e:
                    traceback.print_exc()

    # Missing operation mode
    if not args.web and not args.cli and not args.f and not args.s:
        print("[E] Use at least one of the options -f file, -s directory, --web or --cli")
        sys.exit(1)

    # Write a CSV header
    if not args.nocsv and not alreadyExists:
        writeCSVHeader(resultFile)

    # Process the input lines
    try:
        processLines(lines, resultFile, args.nocsv, args.debug)
    except UnicodeEncodeError as e:
        print("[E] Error while processing some of the values due to unicode decode errors. "
              "Try using python3 instead of version 2.")

    # Write Cache
    if not args.nocsv:
        print("\n[+] Results written to file {0}".format(resultFile))
    print("\n[+] Saving {0} cache entries to file {1}".format(len(cache), args.c))

    # Don't save cache if cache shouldn't be used
    if not args.nocache:
        saveCache(cache, args.c)

    print(Style.RESET_ALL)

