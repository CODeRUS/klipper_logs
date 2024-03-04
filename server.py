#!/usr/bin/python3 -u
# -*- coding: utf-8 -*-

from aiohttp import web

import struct
import logging
import json

import hashlib
from mmap import mmap, ACCESS_READ

import os
import datetime

import random
import html

import tarfile

import gzip
import shutil

from print_config import print_config


chart_n = 0
chart_data_n = 0
collapse_n = 0
collapse_j = 0


MAXBANDWIDTH=25000.
MAXBUFFER=2.
STATS_INTERVAL=5.
TASK_MAX=0.0025

def add_collapse_start(title, classname=''):
  global collapse_n
  collapse_n += 1
  response = f'''<div class="card mb-2 mt-2" style="">
  <div style="transform: rotate(0);" class="card-header d-flex">
    <div>{title}</div>
    <a id="collapseHeader{collapse_n}" class="ms-auto stretched-link collapsed" style="text-decoration:none" href="javascript:void(0)" onclick="collapseToggle('{collapse_n}')">Open spoiler</a>
  </div>
  <div class="card-body text-break collapse {classname}" id="collapseExample{collapse_n}" style="white-space: break-spaces;">'''
  return response

def add_collapse_end(title='', show_close=True):
  global collapse_n
  close_text = 'Close' if show_close else ''
  response = f'''</div>
<div class="card-footer d-flex" style="transform: rotate(0);">
<a id="collapseFooter{collapse_n}" class="ms-auto stretched-link collapsed" href="#collapseHeader{collapse_n}" style="text-decoration:none" onclick="collapseToggle('{collapse_n}')"></a></div>
</div>'''
  return response

def add_collapse_job_start(title, classname=''):
  global collapse_j
  collapse_j += 1
  response = f'''<div class="card mb-2 mt-2" style="">
  <div style="transform: rotate(0);" class="card-header d-flex">
    <div>{title}</div>
    <a id="collapseHeaderJob{collapse_j}" class="ms-auto stretched-link collapsed" style="text-decoration:none" href="javascript:void(0)" onclick="collapseToggleJob('{collapse_j}')">Close spoiler</a>
  </div>
  <div class="card-body text-break collapse show {classname}" id="collapseExampleJob{collapse_j}" style="white-space: break-spaces;">'''
  return response

def add_collapse_job_end(title='', show_close=True):
  global collapse_j
  close_text = 'Close' if show_close else ''
  response = f'''</div>
<div class="card-footer d-flex" style="transform: rotate(0);">
<a id="collapseFooterJob{collapse_j}" class="ms-auto stretched-link collapsed" href="#collapseHeaderJob{collapse_j}" style="text-decoration:none" onclick="collapseToggleJob('{collapse_j}')">Close spoiler</a></div>
</div>'''
  return response

def add_collapse(title, data):
  response = add_collapse_start(title)
  response += data
  response += add_collapse_end(title)
  return response

def add_chart_data(data):
  global chart_data_n
  chart_data_n += 1
  response = f'''<script>
var chart_data_{chart_data_n} = {data};
</script>'''
  return response

def get_chart_data_name():
  global chart_data_n
  return f'chart_data_{chart_data_n}'

def add_freqs_chart(keys, data, title='MCU frequencies'):
  lists = { key: [d[key] for d in data if key in d] for key in keys if key != 'date' }
  est_mhz = { key: round((sum(lists[key]) / len(lists[key])) / 1000000.) for key in lists }
  freq_data = [ { key: d[key] if key == 'date' else ( d[key] - est_mhz[key] * 1000000.) / est_mhz[key] for key in d } for d in data ]
  res = add_chart_data(freq_data)
  res += add_chart({'Microsecond deviation': keys}, title)
  return res

def find_print_restarts(data):
  runoff_samples = {}
  last_runoff_start = last_buffer_time = last_sampletime = 0.
  last_print_stall = 0
  for d in reversed(data):
    # Check for buffer runoff
    sampletime = d['sampletime']
    buffer_time = float(d.get('sysinfo:buffer_time', 0.))
    if (last_runoff_start and last_sampletime - sampletime < 5
        and buffer_time > last_buffer_time):
      runoff_samples[last_runoff_start][1].append(sampletime)
    elif buffer_time < 1.:
      last_runoff_start = sampletime
      runoff_samples[last_runoff_start] = [False, [sampletime]]
    else:
      last_runoff_start = 0.
    last_buffer_time = buffer_time
    last_sampletime = sampletime
    # Check for print stall
    if 'sysinfo:print_stall' in d:
      print_stall = int(d['sysinfo:print_stall'])
      if print_stall < last_print_stall:
        if last_runoff_start:
          runoff_samples[last_runoff_start][0] = True
      last_print_stall = print_stall
  sample_resets = [ sampletime for stall, samples in runoff_samples.values()
                   for sampletime in samples if not stall ]
  return sample_resets

def add_mcu_chart(keys, data, title='MCU bandwidth and load utilization'):
  sample_resets = find_print_restarts(data)

  mcu_load_data = []
  basetime = lasttime = data[0]['sampletime']
  mcu_list = [ k.split(':')[0] for k in keys if k.endswith(':bytes_retransmit') ]
  lastbw = { mcu: float(data[0][f'{mcu}:bytes_write']) + float(data[0][f'{mcu}:bytes_retransmit']) for mcu in mcu_list }

  for d in data:
    st = d['sampletime']
    timedelta = st - lasttime
    if timedelta <= 0.:
      continue

    item = { 'date': d['date'] }

    for mcu in mcu_list:
      br_key = f'{mcu}:bytes_retransmit'
      bw_key = f'{mcu}:bytes_write'
      mta_key = f'{mcu}:mcu_task_avg'
      mts_key = f'{mcu}:mcu_task_stddev'

      if not br_key in d:
        continue

      if not bw_key in d:
        continue

      if not mta_key in d:
        continue

      if not mts_key in d:
        continue

      bw = float(d[bw_key]) + float(d[br_key])
      if bw < lastbw[mcu]:
        lastbw[mcu] = bw
        continue

      load = float(d[mta_key]) + 3 * float(d[mts_key])
      if st - basetime < 15.:
        load = 0.

      pt = float(d['sysinfo:print_time'])
      hb = float(d['sysinfo:buffer_time'])
      if hb >= MAXBUFFER or st in sample_resets:
        hb = 0.
      else:
        hb = 100. * (MAXBUFFER - hb) / MAXBUFFER

      item[f'{mcu}:hostbuffers'] = hb
      item[f'{mcu}:bwdeltas'] = 100. * (bw - lastbw[mcu]) / (MAXBANDWIDTH * timedelta)
      item[f'{mcu}:loads'] = 100. * load / TASK_MAX
      item[f'{mcu}:awake'] = 100. * float(d.get('mcu_awake', 0.)) / STATS_INTERVAL

      lasttime = st
      lastbw[mcu] = bw

    mcu_load_data += [item]

  bandwidth_keys = ('bwdeltas',)
  loads_keys = ('hostbuffers', 'loads', 'awake')
  bw_data_keys = [ f'{mcu}:{key}' for mcu in mcu_list for key in bandwidth_keys ]
  loads_data_keys = [ f'{mcu}:{key}' for mcu in mcu_list for key in loads_keys ]

  res = add_chart_data(mcu_load_data)
  res += add_chart({'Bandwidth': bw_data_keys, 'Loads': loads_data_keys}, title)
  return res


def add_chart(keys, title='Temperature stats'):
  global chart_n
  chart_n += 1
  chart_id = f'chart_{chart_n}'

  chart_data_name = get_chart_data_name()

  response = add_collapse_start(title)
  response += f'<div id="{chart_id}" style="width:100%; height:500px"></div>'
  x_data = json.dumps(keys, separators=(',', ':'))
  response += f'<script>'

  response += f'''
createChart("{chart_id}", "{title}", {chart_data_name}, {x_data});
</script>'''
  response += add_collapse_end(title, False)
  return response
  
def process_moonraker(moonraker):
  logging.info('processing moonraker file %s\n', moonraker)
  response = []
  
  sc = ''

  file = open(moonraker, 'r')

#  keywords = (
#  )

  for l in file:
    line = l.rstrip('\n')
    hline = html.escape(line)
    
    if 'Unsafe Shutdown Count' in line:
      sc = line.split()[-1]

#    if any(ext in line for ext in keywords):
#      response += [hline]

  file.close()
  
  if sc:
    response += [f'Unsafe Shutdown Count: {sc}']

  return response


def process_dmesg(dmesg):
  logging.info('processing dmesg file %s\n', dmesg)
  response = []

  file = open(dmesg, 'r')

  keywords = (
    'Kernel command line',
    'ttyS',
    'spi',
    'btltty',
    'cannot reset',
    'annot enable',
    'cannot disable',
    'disabled by hub',
    'status failed',
    'I/O error',
    'device disconnected',
    'now attached to',
    'device descriptor',
    'device disconnected',
    'New USB device',
    ': Product:',
    ': Manufacturer:',
    ': SerialNumber:',
  )

  try:
    for l in file:
      line = l.rstrip('\n')
      hline = html.escape(line)

      if any(ext in line for ext in keywords):
        response += [hline]
  except:
    pass

  file.close()

  return response
  
def process_debug(debug):
  logging.info('processing debug file %s\n', debug)

  file = open(debug, 'r')
  response = file.readlines()
  
  file.close()
  
  return response


def process_logfile(digest, htmlfile):
  logging.info('processing log file %s to %s\n', digest, htmlfile)
  logfile = f'cache/{digest}.log'
  name = logfile.split('/')[-1]

  mtime = os.path.getmtime(logfile)
  mdate = datetime.datetime.fromtimestamp(mtime)
  expdate = mdate + datetime.timedelta(days = 7)
  expstr = expdate.strftime('%d-%m-%Y')
  expiration_line = f'<i>Logs will expire at {expstr}</i><br>'

  moonraker_name = f'{digest}_moonraker.log'
  moonraker_file = os.path.join('cache', moonraker_name)
  moonraker_exists = os.path.exists(moonraker_file)
  moonraker_line = f'<a href="/klipper_logs/{moonraker_name}">Download moonraker logfile</a><br/>' if moonraker_exists else ''
  
  moonraker_info = process_moonraker(moonraker_file) if moonraker_exists else []

  dmesg_name = f'{digest}_dmesg.log'
  dmesg_file = os.path.join('cache', dmesg_name)
  dmesg_exists = os.path.exists(dmesg_file)
  dmesg_line = f'<a href="/klipper_logs/{dmesg_name}">Download dmesg logfile</a><br/>' if dmesg_exists else ''

  dmesg_info = process_dmesg(dmesg_file) if dmesg_exists else []

  debug_name = f'{digest}_debug.log'
  debug_file = os.path.join('cache', debug_name)
  debug_exists = os.path.exists(debug_file)
  debug_line = f'<a href="/klipper_logs/{debug_name}">Download debug logfile</a><br/>' if debug_exists else ''
  
  debug_info = process_debug(debug_file) if debug_exists else []

  crownest_name = f'{digest}_crownest.log'
  crownest_file = os.path.join('cache', crownest_name)
  crownest_exists = os.path.exists(crownest_file)
  crownest_line = f'<a href="/klipper_logs/{crownest_name}">Download crownest logfile</a><br/>' if crownest_exists else ''

  telegram_name = f'{digest}_telegram.log'
  telegram_file = os.path.join('cache', telegram_name)
  telegram_exists = os.path.exists(telegram_file)
  telegram_line = f'<a href="/klipper_logs/{telegram_name}">Download telegram logfile</a><br/>' if telegram_exists else ''

  response = '''<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Klipper Log Parser</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.2.3/dist/css/bootstrap.min.css" rel="stylesheet" integrity="sha384-rbsA2VBKQhggwzxH7pPCaAqO46MgnOM80zW1RWuH61DGLwZJEdK2Kadq2F9CUG65" crossorigin="anonymous">
<style>
div.code code::before {
  content: counter(listing) ". ";
  display: inline-block;
  width: 8em;
  padding-left: auto;
  margin-left: auto;
  text-align: right;
}
</style>
</head>
<script src="https://cdn.amcharts.com/lib/5/index.js"></script>
<script src="https://cdn.amcharts.com/lib/5/xy.js"></script>
<script src="https://cdn.amcharts.com/lib/5/themes/Animated.js"></script>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.2.3/dist/js/bootstrap.bundle.min.js" integrity="sha384-kenU1KFdBIe4zVF0s0G1M5b4hcpxyD9F7jL+jjXkk+Q2h455rYXK/7HAuoJl+0I4" crossorigin="anonymous"></script>
<script>
function createHtml(html) {
  const template = document.createElement('template');
  template.innerHTML = html;
  const result = template.content.children;
  return result[0];
}

function collapseToggle(idx) {
  const cHeader = document.getElementById(`collapseHeader${idx}`);
  const collapse = document.getElementById(`collapseExample${idx}`);
  const bsCollapse = new bootstrap.Collapse(collapse, {toggle: false});
  const cFooter = document.getElementById(`collapseFooter${idx}`);

  console.log(collapse);

  if (collapse.classList.contains('show')) {
    cHeader.innerHTML = 'Open spoiler';
    cFooter.innerHTML = '';
  } else {
    cHeader.innerHTML = 'Close spoiler';
    cFooter.innerHTML = 'Close spoiler';
  }
  bsCollapse.toggle();
}

function collapseToggleJob(idx) {
  const cHeader = document.getElementById(`collapseHeaderJob${idx}`);
  const collapse = document.getElementById(`collapseExampleJob${idx}`);
  const bsCollapse = new bootstrap.Collapse(collapse, {toggle: false});
  const cFooter = document.getElementById(`collapseFooterJob${idx}`);

  console.log(collapse);

  if (collapse.classList.contains('show')) {
    cHeader.innerHTML = 'Open spoiler';
    cFooter.innerHTML = '';
  } else {
    cHeader.innerHTML = 'Close spoiler';
    cFooter.innerHTML = 'Close spoiler';
  }
  bsCollapse.toggle();
}

var summary = {};
summary.fuckups = 0;

function onLoad() {
  const html = document.getElementsByTagName('html')[0];
//  html.scrollTop = html.scrollHeight;

  const summary_node = document.getElementById("summary");
  summary_node.innerHTML = '';
  if (summary.fuckups > 0) {
    const fuckups = createHtml(`<div class="card alert alert-danger">Unexpected restarts count: ${summary.fuckups}</div>`);
    summary_node.appendChild(fuckups);
  }
  
  if (summary.versions) {
    for (const ver in summary.versions) {
      const atype = summary.versions_ok ? "alert-success" : "alert-warning";
      const version_node = createHtml(`<div class="alert ${atype}" role="alert">${ver} version: ${summary.versions[ver]}</div>`);
      summary_node.appendChild(version_node);
    }
  }
  
  if (summary.restarts.length > 0) {
    const restarts_len = summary.restarts.length;
    const rt_h = createHtml(`<div class="card mb-2 mt-2"><div style="transform: rotate(0);" class="card-header d-flex"><div>Printer restarts. Count: ${restarts_len}</div><a id="collapseHeaderRestarts" class="ms-auto stretched-link collapsed" href="javascript:void(0)" style="text-decoration:none" onclick="collapseToggle('Restarts')">Open spoiler</a></div></div>`);
    const rt_body = createHtml(`<div class="card-body collapse" id="collapseExampleRestarts"></div>`);
    for (let restart = 0; restart < summary.restarts.length; restart++) {
      const rt = createHtml(`<div><a href="#restart_${restart}">${summary.restarts[restart]}</a></div>`);
      rt_body.appendChild(rt);
    }
    rt_h.appendChild(rt_body);
    rt_f = createHtml(`</div><div class="card-footer d-flex" style="transform: rotate(0);"><a id="collapseFooterRestarts" class="ms-auto stretched-link collapsed" href="#collapseHeaderRestarts" style="text-decoration:none" onclick="collapseToggle('Restarts')"></a></div>
</div>`);
    rt_h.appendChild(rt_f);
    summary_node.appendChild(rt_h);
  }
  
  if (summary.jobs.length > 0) {
    const jobs_len = summary.jobs.length;
    const rt_h = createHtml(`<div class="card mb-2 mt-2"><div style="transform: rotate(0);" class="card-header d-flex"><div>Print jobs. Count: ${jobs_len}</div><a id="collapseHeaderJobs" class="ms-auto stretched-link collapsed" href="javascript:void(0)" style="text-decoration:none" onclick="collapseToggle('Jobs')">Open spoiler</a></div></div>`);
    const rt_body = createHtml(`<div class="card-body collapse" id="collapseExampleJobs"></div>`);
    for (let job = 0; job < summary.jobs.length; job++) {
      const rt = createHtml(`<div><a href="#collapseHeaderJob${job+1}">${summary.jobs[job]}</a></div>`);
      rt_body.appendChild(rt);
    }
    rt_h.appendChild(rt_body);
    rt_f = createHtml(`</div><div class="card-footer d-flex" style="transform: rotate(0);"><a id="collapseFooterJobs" class="ms-auto stretched-link collapsed" href="#collapseHeaderJobs" style="text-decoration:none" onclick="collapseToggle('Jobs')"></a></div>
</div>`);
    rt_h.appendChild(rt_f);
    summary_node.appendChild(rt_h);
  }

  if (summary.lastErrors.length > 0) {
    const lr_info = createHtml(`<div class="card mt-3"><h6 class="card-header">Last run errors</h6></div>`);
    const lr_body = createHtml(`<div class="card-body"></div>`);
    for (const lastError in summary.lastErrors) {
      const err = summary.lastErrors[lastError];
      const rt = createHtml(`<div class="alert alert-danger" role="alert"><a class="stretched-link text-black" style="text-decoration:none" href="#${err.id}">${err.text}</a></div>`);
      lr_body.appendChild(rt);
    }
    lr_info.appendChild(lr_body);
    summary_node.appendChild(lr_info);
  }

  if (summary.lastConfig.length > 0) {
    const lr_info = createHtml(`<div class="card mt-3"><h6 class="card-header">Last Config errors</h6></div>`);
    const lr_body = createHtml(`<div class="card-body"></div>`);
    for (let lastConfig = 0; lastConfig < summary.lastConfig.length; lastConfig++) {
      const conf = summary.lastConfig[lastConfig];
      const rt = createHtml(`<div class="alert alert-danger" role="alert"><a class="stretched-link text-black" style="text-decoration:none" href="#collapseHeader${conf.id}">${conf.text}</a></div>`);
      lr_body.appendChild(rt);
    }
    lr_info.appendChild(lr_body);
    summary_node.appendChild(lr_info);
  }

  if (summary.dmesg.length > 0) {
    const lr_info = createHtml(`<div class="card mt-3"><h6 class="card-header">Last Dmesg errors</h6></div>`);
    const lr_body = createHtml(`<div class="card-body"></div>`);
    for (let lastDmesg = 0; lastDmesg < summary.dmesg.length; lastDmesg++) {
      const conf = summary.dmesg[lastDmesg];
      const rt = createHtml(`<div class="alert alert-danger" role="alert">${conf}</div>`);
      lr_body.appendChild(rt);
    }
    lr_info.appendChild(lr_body);
    summary_node.appendChild(lr_info);
  }

  if (summary.config != '') {
    const rt_h = createHtml(`<div class="card mb-2 mt-2"><div style="transform: rotate(0);" class="card-header d-flex"><div>Last Printer Config</div><a id="collapseHeaderConfig" class="ms-auto stretched-link collapsed" href="javascript:void(0)" style="text-decoration:none" onclick="collapseToggle('Config')">Open spoiler</a></div></div>`);
    const rt_body = createHtml(`<div class="card-body collapse" id="collapseExampleConfig"></div>`);
    const config_item = document.getElementById(`collapseExample${summary.config}`);
    rt_body.innerHTML = config_item.innerHTML;
    rt_h.appendChild(rt_body);
    rt_f = createHtml(`</div><div class="card-footer d-flex" style="transform: rotate(0);"><a id="collapseFooterConfig" class="ms-auto stretched-link collapsed" href="#collapseHeaderConfig" style="text-decoration:none" onclick="collapseToggle('Config')"></a></div>
</div>`);
    rt_h.appendChild(rt_f);
    summary_node.appendChild(rt_h);
  }

  html.scrollTop = 0;

  const scripts = document.getElementsByTagName('script');
  for (let i=0; i < scripts.length; i++) {
    const script = scripts[0];
    if (script != undefined && script && script.parentNode)
      script.parentNode.removeChild(script);
  }
  
  if (location.hash) {
    console.log('hash:', location.hash);
  }
}
function createChart(name, title, data, keys) {
const root = am5.Root.new(name);
root.utc = true;
root.setThemes([
  am5themes_Animated.new(root)
]);
const chart = root.container.children.push(am5xy.XYChart.new(root, {
  panX: true,
  panY: false,
  wheelX: "panX",
  wheelY: "zoomX",
  pinchZoomX:true
}));

chart.children.unshift(am5.Label.new(root, {
  text: title,
  fontSize: 18,
  fontWeight: "500",
  textAlign: "center",
  x: am5.percent(50),
  paddingTop: -20,
}));
const cursor = chart.set("cursor", am5xy.XYCursor.new(root, {
  behavior: "none"
}));
cursor.lineY.set("visible", false);
const xAxis = chart.xAxes.push(am5xy.DateAxis.new(root, {
  baseInterval: { timeUnit: "second", count: 1 },
  renderer: am5xy.AxisRendererX.new(root, {}),
  tooltip: am5.Tooltip.new(root, {})
}));
let opposite = false;
for (const axis in keys) {
  const yAxis = chart.yAxes.push(am5xy.ValueAxis.new(root, {
    renderer: am5xy.AxisRendererY.new(root, {
      opposite: opposite
    })
  }));
  opposite = !opposite;
  const yAxisLabel = am5.Label.new(root, {
    rotation: -90,
    text: axis,
    y: am5.p50,
    centerX: am5.p50
  })
  yAxis.children.unshift(
    yAxisLabel
  );
  for (const key in keys[axis]) {
    const keyName = keys[axis][key];
    if (keyName == "date") {
      continue;
    }
    const series = chart.series.push(am5xy.LineSeries.new(root, {
      name: keyName,
      xAxis: xAxis,
      yAxis: yAxis,
      valueYField: keyName,
      valueXField: "date",
      tooltip: am5.Tooltip.new(root, {
        labelText: "{name}: {valueY}"
      })
    }));
    if (keyName.endsWith(':target')) {
      series.strokes.template.setAll({
        strokeDasharray: [10]
      });
    }
    series.data.setAll(data);
  }
}
const scrollbar = chart.set("scrollbarX", am5xy.XYChartScrollbar.new(root, {
  orientation: "horizontal",
  height: 30 
}));
const sbDateAxis = scrollbar.chart.xAxes.push(am5xy.DateAxis.new(root, {
  baseInterval: {
    timeUnit: "second",
    count: 1
  },
  renderer: am5xy.AxisRendererX.new(root, {})
}));
const sbValueAxis = scrollbar.chart.yAxes.push(
  am5xy.ValueAxis.new(root, {
    renderer: am5xy.AxisRendererY.new(root, {})
  })
);
const legend = chart.bottomAxesContainer.children.push(
  am5.Legend.new(root, {
    centerX: am5.p50,
    x: am5.p50
  })
);
legend.data.setAll(chart.series.values);

return () => {
  root.dispose()
}
}
//function onLoad() {
//  const html = document.getElementsByTagName('html')[0];
//  html.scrollTop = html.scrollHeight;
//}
</script>'''
  response += f'''
<body onload="onLoad()">
<svg xmlns="http://www.w3.org/2000/svg" style="display: none;">
  <symbol id="check-circle-fill" viewBox="0 0 16 16">
    <path d="M16 8A8 8 0 1 1 0 8a8 8 0 0 1 16 0zm-3.97-3.03a.75.75 0 0 0-1.08.022L7.477 9.417 5.384 7.323a.75.75 0 0 0-1.06 1.06L6.97 11.03a.75.75 0 0 0 1.079-.02l3.992-4.99a.75.75 0 0 0-.01-1.05z"/>
  </symbol>
  <symbol id="info-fill" viewBox="0 0 16 16">
    <path d="M8 16A8 8 0 1 0 8 0a8 8 0 0 0 0 16zm.93-9.412-1 4.705c-.07.34.029.533.304.533.194 0 .487-.07.686-.246l-.088.416c-.287.346-.92.598-1.465.598-.703 0-1.002-.422-.808-1.319l.738-3.468c.064-.293.006-.399-.287-.47l-.451-.081.082-.381 2.29-.287zM8 5.5a1 1 0 1 1 0-2 1 1 0 0 1 0 2z"/>
  </symbol>
  <symbol id="exclamation-triangle-fill" viewBox="0 0 16 16">
    <path d="M8.982 1.566a1.13 1.13 0 0 0-1.96 0L.165 13.233c-.457.778.091 1.767.98 1.767h13.713c.889 0 1.438-.99.98-1.767L8.982 1.566zM8 5c.535 0 .954.462.9.995l-.35 3.507a.552.552 0 0 1-1.1 0L7.1 5.995A.905.905 0 0 1 8 5zm.002 6a1 1 0 1 1 0 2 1 1 0 0 1 0-2z"/>
  </symbol>
</svg>
<div style="font-family:monospace" class="container-fluid">
<p class="text-start">
<a href="/klipper_logs">Home</a><br/>
{expiration_line}
<a href="/klipper_logs/{name}">Download klippy logfile</a><br/>
{moonraker_line}
{dmesg_line}
{debug_line}
{crownest_line}
{telegram_line}
</p><div class="card mb-3"><h5 class="card-header">Summary info</h5><div class="card-body" id="summary"><div class="card-child">Please wait, page is loading</div></div></div>'''

  if len(moonraker_info) > 0:
    response += add_collapse_start('Moonraker info')
    for d in moonraker_info:
      response += d + '<br>'
    response += add_collapse_end()

  if len(dmesg_info) > 0:
    response += add_collapse_start('Dmesg info')
    for d in dmesg_info:
      response += d + '<br>'
    response += add_collapse_end()

  if len(debug_info) > 0:
    response += add_collapse_start('Debug info')
    for d in debug_info:
      response += d
    response += add_collapse_end()

  date = 0
  pydate = None
  time_offset = 0
  print_offset = 0
  mcu_stats = ''
  mcu_keys = []
  mcu_data = []
  
  collapse_open = False

  def filter_data_keys(keys):
    nonlocal mcu_keys
    filter_keys = [ k for k in mcu_keys if k.split(':')[-1] in keys ]
    return filter_keys

  def filter_data(keys):
    nonlocal mcu_data
    filter_keys = filter_data_keys(keys)
    return ( filter_keys, [ { key: d[key] for key in d if key in filter_keys } for d in mcu_data ] )

  def get_charts():
    nonlocal mcu_data
    nonlocal mcu_keys

    res = add_chart_data(mcu_data)

    filter_temp_keys = ('date', 'temp', 'target', 'pwm', 'fan_speed')
    temp_keys = filter_data_keys(filter_temp_keys)
    pwm_keys = [ k for k in temp_keys if k.split(':')[-1] in ('pwm', 'fan_speed') ]
    temp_keys = [ k for k in temp_keys if k.split(':')[-1] in ('temp', 'target') ]
    res += add_chart({ 'Temperature': temp_keys, 'PWM %': pwm_keys })

    filter_load_keys = ('date', 'cpudelta', 'sysload', 'memavail')
    load_keys = filter_data_keys(filter_load_keys)
    mem_keys = [ k for k in load_keys if k.split(':')[-1] in ('memavail',) ]
    sysload_keys = [ k for k in load_keys if k.split(':')[-1] in ('sysload',) ]
    cpudelta_keys = [ k for k in load_keys if k.split(':')[-1] in ('cpudelta',) ]
    res += add_chart({ 'Load (% of all cores)': sysload_keys, 'Available memory (MB)': mem_keys, 'CPU delta': cpudelta_keys }, 'System load utilization')

    filter_freq_keys = ('date', 'freq', 'adj')
    freq_keys, freq_data = filter_data(filter_freq_keys)
    res += add_freqs_chart(freq_keys[1:], freq_data)

    filter_mcu_keys = ('date', 'bytes_write', 'bytes_retransmit', 'mcu_task_avg', 'mcu_task_stddev')
    mcu_load_keys = filter_data_keys(filter_mcu_keys)
    res += add_mcu_chart(mcu_load_keys[1:], mcu_data)

    mcu_data = []
    mcu_keys = []

    return res

  config = False
  sent = False
  receive = False
  received = False
  queue = False
  build_config = False
  oid = False
  mesh = False
  autotune = False
  webhooks = False
  traceback = False
  mcu_got = False
  no_such = False
  no_port = False
  print_stats = False
  prediction = False

  print_stats_keys = (';', 'extruder:', 'pressure_advance_smooth_time:', 'toolhead:', 'max_accel:', 'max_accel_to_decel:', 'square_corner_velocity:', 'new minimum rtt', 'Ignoring clock sample')

  summary = {}
  summary['fuckups'] = 0
  summary['config'] = ''
  summary['restarts'] = []
  summary['jobs'] = []

  summary['dmesg'] = []
  dmesg_red = (
    'disabled by hub',
    'I/O error'
  )
  for dline in dmesg_info:
    if any(dext in dline for dext in dmesg_red):
      summary['dmesg'] += [dline]

  anchor_id = 0

  summary_errors = []
  summary_config = []
  summary_config_id = ''
  
  versions = {}

  last_build_config = ''

  last_config_id = 0

  file = open(logfile, 'rb')
  out = open(htmlfile, 'w+')
  ln = 0

  for binline in file:
    line = binline.decode().rstrip('\n')
    hline = html.escape(line)

    if '.crealityprint' in line:
      out.close()
      out = open(htmlfile, 'w')
      response = 'Fucking Sonic Pad'
      mcu_data = []
      break

    if len(line) == 0:
      response += '<span><br/></span>'
      continue

    newresponse = ''

    if line.endswith('Starting Klippy...') or line.startswith('Starting Klippy...') or line.startswith('Restarting printer'):
      fucked = False

#      l = ''
#      if not line.startswith("Stats "):
#        l = line.split('Starting Klippy...')[0].split('Restarting printer')[0]

      bl = binline.split(b'Starting Klippy')[0]
      if len(bl) > 0 and bl[-1] == 0:
        fucked = True

      if autotune:
        response += add_collapse_end('Autotune TMC')
        autotune = False
        fucked = True

      if sent:
        response += add_collapse_end('Sent')
        sent = False
        fucked = True

      if receive:
        response += add_collapse_end('Receive')
        receive = False
        fucked = True

      if mcu_got:
        response += add_collapse_end('MCU receive')
        mcu_got = False
        fucked = True

      if received:
        response += add_collapse_end('Received')
        received = False
        fucked = True

      if queue:
        response += add_collapse_end('Last moves')
        queue = False
        fucked = True

      if oid:
        response += add_collapse_end('MCU clock')
        oid = False
        fucked = True

      if mesh:
        response += add_collapse_end('Bed mesh')
        mesh = False
        fucked = True

      if config:
        response += add_collapse_end('Config')
        config = False
        fucked = True

      if webhooks:
        response += add_collapse_end('Webhooks')
        webhooks = False
        fucked = True

      if print_stats:
        response += add_collapse_end('Print comments')
        print_stats = False
        fucked = True

      if traceback:
        response += '</div>'
        traceback = False
        fucked = True

      if no_such:
        response += '</div>'
        no_such = False
        fucked = True

      if no_port:
        response += '</div>'
        no_port = False
        fucked = True

      if config:
        response += add_collapse_end('Config')
        config = False
        fucked = True

      if prediction:
        response += add_collapse_end('Resetting prediction variance')
        prediction = False
        fucked = True

      if line.startswith("Stats "):
        fucked = True

      if fucked:
        summary['fuckups'] += 1
        response += f'<div class="alert alert-danger" role="alert">Unexpected end of block. System was poweroff?</div>'
        if not line.startswith("Stats "):
          l2 = line.split('Starting Klippy...')[0].split('Restarting printer')[0]
          response += html.escape(l2)
        line = 'Starting Klippy...'
        hline = line


    if print_stats and not any(line.startswith(k) for k in print_stats_keys) and not line.startswith('Stats '):
      print_stats = False
      response += add_collapse_end('Print comments')

    if no_such and 'No such file or directory' not in line:
      no_such = False
      response += '</div>'

    if no_port and 'Unable to open serial port' not in line:
      no_port = False
      response += '</div>'

    if autotune and not line.startswith('autotune_tmc'):
      autotune = False
      response += add_collapse_end('Autotune TMC')

    if receive and not line.startswith('Receive: '):
      receive = False
      response += add_collapse_end('Receive')

    if mcu_got and ': got {' not in line:
      mcu_got = False
      response += add_collapse_end('MCU receive')

    if webhooks and not line.startswith('webhooks: '):
      webhooks = False
      response += add_collapse_end('Webhooks')

    if line.startswith('Stats '):
      d1 = line.replace('sysload=', 'sysinfo: sysload=').split()
      item = {}
      name = ''

      st = round(float(d1[1][:-1]) * 10) / 10
      if time_offset == 0:
        time_offset = st

      sampletime = round((st - time_offset) * 10) / 10
      timestamp = int((sampletime + date) * 1000)

      item['sampletime'] = sampletime
      item['date'] = timestamp

      mcu_stats += f'{sampletime} '

      for it in d1[3:]:
        if it.endswith(':'):
          name = it[:-1]
          mcu_stats += f'[{name}]: '

        else:
          tmp = it.split('=')
          if len(tmp) == 2:
            key, v = tmp
            value = float("{:.6f}".format(float(v)))

            if key == 'pwm':
              value = int(value * 100)

            if key == 'fan_speed':
              value = int(value * 100)

            elif key == 'memavail':
              value = value / 1024.

            elif key == 'sysload':
              value = value * 100.

            item[f'{name}:{key}'] = value
            mcu_stats += f'{key}: {v} '

      print_offset = st - (round(float(item['sysinfo:print_time']) * 100) / 100)

      if 'sysinfo:cputime' in item:
        if len(mcu_data) > 0:
          lasttime = mcu_data[-1]['sampletime']
          timedelta = sampletime - lasttime

          lastcputime = mcu_data[-1]['sysinfo:cputime']
          cputime = item['sysinfo:cputime']
          cpudelta = max(0., min(1.5, (cputime - lastcputime) / timedelta))

          item['sysinfo:cpudelta'] = cpudelta * 100.
        else:
          item['sysinfo:cpudelta'] = 0

      filter_keys = (
        'date', 'sampletime',
        'temp', 'target', 'pwm', 'fan_speed',
        'freq', 'adj',
        'cputime', 'cpudelta', 'sysload', 'memavail',
        'buffer_time', 'print_stall', 'bytes_write', 'bytes_retransmit', 'mcu_task_avg', 'mcu_task_stddev', 'print_time'
        )
      item = { k: item[k] for k in item if k.split(':')[-1] in filter_keys }

      for key in item:
        if not key in mcu_keys:
          mcu_keys += [key]

      mcu_stats += '\n'
      mcu_data += [item]

    elif line == 'bed_mesh: generated points':
      mesh = True
      response += add_collapse_start('Bed Mesh generated points')

    elif mesh and (' Tool Adjusted ' in line or ' | (' in line):
      response += hline + '\n'

    elif line == '========= Last MCU build config =========':
      build_config = True
      response += add_collapse_start('Last Build Config')
      last_build_config = ''

    elif line == '=======================' and build_config:
      build_config = False
      response += add_collapse_end('Last Build Config')

      response += add_collapse_start('Firmware configuration')
      config_out = print_config(last_build_config)
      for c in config_out:
        response += c + '<br>'
      response += add_collapse_end('Firmware configuration')

    elif build_config:
      response += hline + '\n'
      last_build_config += line + '\n'

    elif line == '===== Config file =====':
      config = True
      response += add_collapse_start('Config', 'code')
      global collapse_n
      last_config_id = collapse_n
      summary['config'] = last_config_id

    elif line == '=======================' and config:
      config = False
      response += add_collapse_end('Config')

    elif config:
      summary_config += [line]
      summary_config_id = anchor_id
      response += hline + '</br>'

    elif 'No such file or directory' in line and not no_such and not traceback:
      no_such = True
      anchor_id += 1
      response += f'<div id="anchor{anchor_id}" class="card card-body text-break alert alert-danger" style="white-space: break-spaces">{hline}\n'
      summary_errors += [{'id':f'anchor{anchor_id}', 'text':hline.strip()}]

    elif no_such:
      response += hline + '\n'

    elif 'Unable to open serial port' in line and not no_port and not traceback:
      no_port = True
      anchor_id += 1
      response += f'<div id="anchor{anchor_id}" class="card card-body text-break alert alert-danger" style="white-space: break-spaces">{hline}\n'
      summary_errors += [{'id':f'anchor{anchor_id}', 'text':hline.strip()}]

    elif no_port:
      response += hline + '\n'

    elif line.startswith('Traceback ') and not traceback:
      traceback = True
      anchor_id += 1
      response += f'<div id="anchor{anchor_id}" class="card card-body text-break alert alert-danger" style="white-space: break-spaces">{hline}\n'

    # elif line.lstrip().startswith('raise ') and traceback:
    #   traceback = False
    #   response += f'{line}</div>'

    elif line.lstrip().lower().split()[0].endswith('error:') and traceback:
      traceback = False
      response += f'{hline}</div>'
      summary_errors += [{'id':f'anchor{anchor_id}', 'text':hline.strip()}]

    elif traceback:
      response += hline + '\n'

    elif line.startswith('Sent '):
      if not sent:
        response += add_collapse_start('Sent')
        sent = True

      response += f'{hline}<br/>'

    elif any(line.startswith(k) for k in print_stats_keys):
      if not print_stats:
        response += add_collapse_start('Print comments')
        print_stats = True

      response += f'{hline}<br/>'

    elif line.startswith('Receive: '):
      if not receive:
        response += add_collapse_start('Receive')
        receive = True

      response += f'{hline}<br/>'

    elif ': got {' in line:
      if not mcu_got:
        response += add_collapse_start('MCU receive')
        mcu_got = True

      response += f'{hline}<br/>'

    elif line.startswith('Received '):
      if not received:
        response += add_collapse_start('Received')
        received = True

      response += f'{hline}<br/>'

    elif line.startswith('queue_step '):
      if not queue:
        response += add_collapse_start('Queue steps')
        queue = True

      response += f'{hline}<br/>'

    elif line.startswith('move '):
      if not queue:
        response += add_collapse_start('Last moves')
        queue = True

      response += f'{hline}<br/>'

    elif "got {'oid': " in line:
      if not oid:
        response += add_collapse_start('MCU clock')
        oid = True

      response += f'{hline}<br/>'

    elif line.startswith('autotune_tmc'):
      if not autotune:
        response += add_collapse_start('Autotune TMC')
        autotune = True

      response += f'{hline}<br/>'

    elif line.startswith('Resetting prediction variance'):
      if not prediction:
        response += add_collapse_start('Resetting prediction variance')
        prediction = True

      response += f'{hline}<br/>'

    elif line.startswith('webhooks: '):
      if not webhooks:
        response += add_collapse_start('Webhooks')
        webhooks = True

      response += f'{hline}<br/>'

    else:
      if autotune:
        response += add_collapse_end('Autotune TMC')
        autotune = False

      if prediction:
        response += add_collapse_end('Resetting prediction variance')
        prediction = False

      if sent:
        response += add_collapse_end('Sent')
        sent = False

      if receive:
        response += add_collapse_end('Receive')
        receive = False

      if mcu_got:
        response += add_collapse_end('MCU receive')
        mcu_got = False

      if received:
        response += add_collapse_end('Received')
        received = False

      if queue:
        response += add_collapse_end('Last moves')
        queue = False

      if oid:
        response += add_collapse_end('MCU clock')
        oid = False

      if mesh:
        response += add_collapse_end('Bed mesh')
        mesh = False

      if print_stats:
        response += add_collapse_end('Print comments')
        print_stats = False

      if line.startswith('Start printer'):
        if len(mcu_stats) > 0:
          response += add_collapse('MCU Stats', mcu_stats)
          mcu_stats = ''

        if len(mcu_data) > 0:
          newresponse += get_charts()

        date = int(float(line.split()[8][1:]))
        summary['lastConfig'] = []
        summary['lastErrors'] = []
        summary.setdefault('restarts', []).append(' '.join(line.split()[3:-2]))
        restart_id = len(summary['restarts']) - 1

        summary_errors = []
        summary_config = []

        time_offset = round(float(line.split()[-1][:-1]) * 10) / 10
        dtline = ' '.join(line.split()[3:8])
        try:
          dat = datetime.datetime.strptime(dtline, '%a %b %d %H:%M:%S %Y')
          pydate = dat.replace(tzinfo=datetime.timezone.utc)
        except:
          pass
        newresponse += f'<div class="alert alert-success" role="alert" id="restart_{restart_id}">{line}</div>'

      elif 'Log rollover at' in line:
        dtline = ' '.join(line.split()[4:9])
        try:
          dat = datetime.datetime.strptime(dtline, '%a %b %d %H:%M:%S %Y')
          pydate = dat.replace(tzinfo=datetime.timezone.utc)
          date = pydate.timestamp()
          time_offset = 0
        except:
          pass
          
        dateinfo = line.split()[4:-1]
        datestr = ' '.join(dateinfo)
        
        summary.setdefault('restarts', []).append(datestr)
        restart_id = len(summary['restarts']) - 1
        
        summary.setdefault('jobs', []).append(datestr)
        job_id = len(summary['jobs']) - 1

        dtline = ' '.join(line.split()[1:-1])
        newresponse += f'<div class="alert alert-success" role="alert" id="restart_{restart_id}">{dtline}</div>'
        
        newresponse += add_collapse_job_start(f'Possible print job rollover at: {datestr}')
        collapse_open = True
        
#        newresponse += f'<div class="alert alert-success" role="alert" id="job_{job_id}">Possible print job rollover at: {datestr}</div>'

      elif line.startswith('Loaded MCU'):
        if len(mcu_stats) > 0:
          response += add_collapse('MCU Stats', mcu_stats)
          mcu_stats = ''

        if len(mcu_data) > 0:
          newresponse += get_charts()

        d1 = line.split("'")
        d2 = line.split()
        mcuname = d1[1]
        mcuversion = line.split('(')[1].split('/')[0].strip()
        versions[mcuname] = mcuversion
        
        newresponse += f'<div class="alert alert-warning" role="alert">MCU {mcuname} version {mcuversion}</div>'

      elif line.startswith('Virtual sdcard ('):
        newresponse += add_collapse_start('Virtual sdcard buffer')
        t = "): '"
        if "n'" in line:
          t = "): n'"
        newresponse += html.escape(line.split("'")[1][:-1].replace('\\r', '').replace('\\n', '\n'))
        newresponse += add_collapse_end('')

      elif line.startswith('Upcoming ('):
        newresponse += add_collapse_start('Virtual sdcard upcoming buffer')
        newresponse += html.escape(line.split("'")[1][:-1].replace('\\r', '').replace('\\n', '\n'))
        newresponse += add_collapse_end('')

      elif 'at shutdown time' in line:
        newresponse += hline.rstrip() + '<br>'
        stime = round(float(line.split()[-4][:-1]) * 100) / 100 + print_offset - time_offset
        dt = pydate + datetime.timedelta(seconds=stime)
        tline = dt.strftime('%a %b %d %H:%M:%S %Y')
        newresponse += f'<div class="alert alert-danger" role="alert">Shutdown time {tline}</div>'

      elif line.startswith('Exiting SD card'):
        if len(mcu_stats) > 0:
          response += add_collapse('MCU Stats', mcu_stats)
          mcu_stats = ''

        if len(mcu_data) > 0:
          newresponse += get_charts()
          
        newresponse += f'<div class="alert alert-warning" role="alert">{hline}</div>'
          
        newresponse += add_collapse_job_end(f'{hline}')
        collapse_open = False

      elif line.startswith('Starting SD card'):
        if len(mcu_stats) > 0:
          response += add_collapse('MCU Stats', mcu_stats)
          mcu_stats = ''

        timestr = ''

        if len(mcu_data) > 0:
          pdate = mcu_data[-1]['date'] / 1000
          mdate = datetime.datetime.fromtimestamp(pdate)
          timestr = mdate.strftime('%a %b %d %H:%M:%S %Y')
          newresponse += get_charts()
          
        summary.setdefault('jobs', []).append(timestr)
        job_id = len(summary['jobs']) - 1
        
        if collapse_open:
          newresponse += add_collapse_job_end()
          
        newresponse += add_collapse_job_start(f'{hline} at: {timestr}')
        collapse_open = True

#        newresponse += f'<div class="alert alert-warning" role="alert" id="job_{job_id}">{hline} at: {timestr}</div>'

      elif line.startswith('Finished SD card print'):
        newresponse += f'<div class="alert alert-success" role="alert">{hline}</div>'

      elif line.startswith('Restarting printer'):
        newresponse += f'<div class="alert alert-warning" role="alert">{hline}</div>'

      elif line.startswith('Attempting MCU'):
        newresponse += f'<div class="alert alert-warning" role="alert">{hline}</div>'

      elif line.endswith('Starting serial connect'):
        newresponse += f'<div class="alert alert-warning" role="alert">{hline}</div>'

      elif line.startswith('Git version'):
        newresponse += f'<div class="alert alert-warning" role="alert">{hline}</div>'
        versions['git'] = line.split()[-1][1:-1]
        newresponse += add_collapse_start('Git info')
        
      elif line.startswith('Tracked URL: '):
        newresponse += hline.rstrip() + '<br>'
        newresponse += add_collapse_end()

      elif line.startswith('Python:'):
        newresponse += f'<div class="alert alert-secondary" role="alert">{hline}</div>'
        
      elif line.startswith('CPU:'):
        newresponse += f'<div class="alert alert-secondary" role="alert">{hline}</div>'

      elif line.startswith('Move out of range'):
        newresponse += f'<div class="alert alert-danger" role="alert">{hline}</div>'

      elif line.startswith('Must home'):
        newresponse += f'<div class="alert alert-danger" role="alert">{hline}</div>'

      elif line.startswith('BLTouch failed'):
        newresponse += f'<div class="alert alert-danger" role="alert">{hline}</div>'

      elif line.startswith('Unable to parse'):
        newresponse += f'<div class="alert alert-danger" role="alert">{hline}</div>'

      elif any(t in line for t in ("' shutdown: ", "Got EOF ", "Got error ")):
        anchor_id += 1
        newresponse += f'<div id="anchor{anchor_id}" class="alert alert-danger" role="alert">{hline}</div>'
        summary_errors += [{'id':f'anchor{anchor_id}', 'text':hline.strip()}]

      elif line.startswith('Timeout with MCU'):
        anchor_id += 1
        et = round(float(line.split('eventtime=')[1].split(')')[0]) * 10) / 10
        seconds = et - time_offset
        # eventtime = round((et - time_offset) * 10) / 10
        # timestamp = int(eventtime + date)
        # mdate = datetime.datetime.fromtimestamp(timestamp)

        timestr = ''
        try:
          dt = pydate + datetime.timedelta(seconds=seconds)
          timestr = dt.strftime('%a %b %d %H:%M:%S %Y')
        except:
          pass

        newline = f'{hline.strip()} ({timestr})'
        newresponse += f'<div id="anchor{anchor_id}" class="alert alert-danger" role="alert">{newline}</div>'
        summary_errors += [{'id':f'anchor{anchor_id}', 'text':newline.strip()}]

      elif line.startswith('Transition to shutdown state'):
        anchor_id += 1
        newresponse += f'<div id="anchor{anchor_id}" class="alert alert-danger" role="alert">{hline}</div>'
        summary_errors += [{'id':f'anchor{anchor_id}', 'text':hline.strip()}]

      elif 'Warning!)' in line:
        anchor_id += 1
        newresponse += f'<div id="anchor{anchor_id}" class="alert alert-danger" role="alert">{hline}</div>'
        summary_errors += [{'id':f'anchor{anchor_id}', 'text':hline.strip()}]

      elif 'Error!)' in line:
        anchor_id += 1
        newresponse += f'<div id="anchor{anchor_id}" class="alert alert-danger" role="alert">{hline}</div>'
        summary_errors += [{'id':f'anchor{anchor_id}', 'text':hline.strip()}]

      elif 'Shutdown!)' in line:
        anchor_id += 1
        newresponse += f'<div id="anchor{anchor_id}" class="alert alert-danger" role="alert">{hline}</div>'
        summary_errors += [{'id':f'anchor{anchor_id}', 'text':hline.strip()}]

      elif line.startswith('Starting Klippy'):
        if len(mcu_stats) > 0:
          response += add_collapse('MCU Stats', mcu_stats)
          mcu_stats = ''

        if len(mcu_data) > 0:
          newresponse += get_charts()

        newresponse += f'<div class="alert alert-success" role="alert">{hline}</div>'

      elif line.endswith('Starting Klippy...'):
        if len(mcu_data) > 0:
          newresponse += get_charts()

        newresponse += '<div class="alert alert-success" role="alert">Starting Klippy...</div>'

      elif line.startswith('Args: ['):
        args = json.loads(line[6:].replace("'", '"'))
        newresponse += add_collapse_start('Args')
        newresponse += ' '.join(args)
        newresponse += add_collapse_end('Args')
        
#        elif 'reports GSTAT:' in line:
#            newresponse += f'<pre  style="background: #f00;color:#fff">{line}</pre>'

      elif len(line.strip()) > 0:
        newresponse += hline.rstrip() + '<br>'

    if newresponse:
      response += newresponse

    ln += 1

    if ln % 50000 == 0:
      out.write(response)
      out.flush()
      response = ''

  file.close()

  if print_stats:
    response += add_collapse_end('Print comments')
    print_stats = False

  if len(mcu_stats) > 0:
    response += add_collapse('MCU Stats', mcu_stats)
    mcu_stats = ''

  if len(mcu_data) > 0:
    response += get_charts()
    
  if collapse_open:
    response += add_collapse_job_end()

  out.write(response)
  out.flush()

  summary['lastErrors'] = summary_errors
  last_config = []
  mcu_serials = []
  mcu_name = ''
  section_name = ''
  for l in summary_config:
    line = l.strip()
    if line.startswith('['):
      section_name = line[1:-1]
    if line.startswith('[mcu '):
      mcu_name = line.split()[-1][:-1]

    if line.startswith('serial ='):
      serial = line.split()[-1]
      if serial in mcu_serials:
        last_config += [{'id': f'collapseHeader{last_config_id}', 'text':f'mcu {mcu_name} serial {serial} already used'}]

      if '<' in line:
        last_config += [{'id': f'collapseHeader{last_config_id}', 'text':f'mcu {mcu_name} serial not filled and contains template value: {html.escape(serial)}'}]

      if 'ttyUSB' in line or 'ttyACM' in line:
        last_config += [{'id': f'collapseHeader{last_config_id}', 'text':f'mcu {mcu_name} serial {serial} may not work correctly, use serial/by-id instead'}]

      mcu_serials += [serial]

    elif line.startswith('rotation_distance =') and section_name.startswith('stepper'):
      dist = line.split()[-1]
      if '.' in dist:
        last_config += [{'id': f'collapseHeader{last_config_id}', 'text': f'[{section_name}] contains decimal rotation_distance = {dist}'}]

  summary['lastConfig'] = last_config
  summary['versions'] = versions.copy()
  summary['versions_ok'] = False
  
  if 'git' in versions:
    git_version = versions.pop('git')
    for ver in versions:
      if git_version.startswith(versions[ver]):
        summary['versions_ok'] = True
        break

  response = f'''<script>
summary = {json.dumps(summary)};
</script>'''

#  if len(response) > 100:
#    response += '</body></html>'
  out.write(response)

  out.flush()
  out.close()

async def handle_index(request: web.Request) -> web.StreamResponse:
  return web.FileResponse("index.html", chunk_size=256 * 1024)

async def handle_upload(request: web.Request) -> web.StreamResponse:
  return web.FileResponse("upload.html", chunk_size=256 * 1024)

async def handle_getlogs(request: web.Request) -> web.StreamResponse:
  return web.FileResponse("getlogs.txt", chunk_size=256 * 1024)

async def handle_getlogdev(request: web.Request) -> web.StreamResponse:
  return web.FileResponse("getlogdev.txt", chunk_size=256 * 1024)
  
async def handle_lang(request: web.Request) -> web.StreamResponse:
  lang = request.match_info.get("lang", "en")
#  if not os.path.exists(f'index_{lang}.json'):
#    lang = 'en'
  return web.FileResponse(f'index_{lang}.json', chunk_size=256 * 1024)

def sizeof_fmt(num, suffix="B"):
  for unit in ("", "K", "M"):
    if abs(num) < 1024.0:
      return f"{num:3.1f}{unit}{suffix}"
    num /= 1024.0
  return f"{num:.1f}Yi{suffix}"


async def handle_list(request: web.Request) -> web.StreamResponse:
  response = '''<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Klipper Log Parser</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.2.3/dist/css/bootstrap.min.css" rel="stylesheet" integrity="sha384-rbsA2VBKQhggwzxH7pPCaAqO46MgnOM80zW1RWuH61DGLwZJEdK2Kadq2F9CUG65" crossorigin="anonymous">
</head>
<script src="https://cdn.amcharts.com/lib/5/index.js"></script>
<script src="https://cdn.amcharts.com/lib/5/xy.js"></script>
<script src="https://cdn.amcharts.com/lib/5/themes/Animated.js"></script>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.2.3/dist/js/bootstrap.bundle.min.js" integrity="sha384-kenU1KFdBIe4zVF0s0G1M5b4hcpxyD9F7jL+jjXkk+Q2h455rYXK/7HAuoJl+0I4" crossorigin="anonymous"></script>
<body>
<div class="container-fluid">
'''

  files = [f for f in os.listdir('cache') if f.endswith('.log') and not '_' in f]
  files.sort(key=lambda x: os.path.getmtime(f'cache/{x}'))

  for file in reversed(files):
    digest = file.split('.')[0]
    htmlfile = f'cache/{file}'
    logfile = f'cache/{digest}.log'
    moonrakerfile = f'cache/{digest}_moonraker.log'
    dmesgfile = f'cache/{digest}_dmesg.log'
    debugfile = f'cache/{digest}_debug.log'

    mtime = os.path.getmtime(logfile)
    mdate = datetime.datetime.fromtimestamp(mtime)
    timestr = mdate.strftime('%d-%m-%Y %H:%M:%S')

    response += '<div class="row mb-2 mt-2 mx-1 row-cols-5">'

    response += f'<a type="button" class="btn btn-outline-secondary col" href="/klipper_logs/{digest}">{timestr}</button>'

    klippysize = sizeof_fmt(os.stat(logfile).st_size)
    response += f'<a type="button" class="btn btn-outline-secondary col" href="/klipper_logs/{digest}.log">klippy.log ({klippysize})</button>'

    if os.path.exists(moonrakerfile):
      moonrakersize = sizeof_fmt(os.stat(moonrakerfile).st_size)
      response += f'<a type="button" class="btn btn-outline-secondary col" href="/klipper_logs/{digest}_moonraker.log">moonraker.log ({moonrakersize})</button>'

    if os.path.exists(dmesgfile):
      dmesgsize = sizeof_fmt(os.stat(dmesgfile).st_size)
      response += f'<a type="button" class="btn btn-outline-secondary col" href="/klipper_logs/{digest}_dmesg.log">dmesg.log ({dmesgsize})</button>'

    if os.path.exists(debugfile):
      debugsize = sizeof_fmt(os.stat(debugfile).st_size)
      response += f'<a type="button" class="btn btn-outline-secondary col" href="/klipper_logs/{digest}_debug.log">debug.log ({debugsize})</button>'

    response += '</div>'

  response = web.Response(text=response)
  response.headers['Content-Type'] = 'text/html'
  return response  

async def handle_log(request: web.Request) -> web.StreamResponse:
  name = request.match_info.get("name", "invalid")
  logfile = f'cache/{name}.log'
  outfile = f'cache/{name}.html'
#  gzfile = f'cache/{name}.html.gz'
  logging.info('serving log file %s\n', logfile)
  if os.path.exists(logfile):
    logging.info('existing log file %s\n', logfile)
    if os.path.exists(outfile) and os.path.getsize(outfile) < 1000:
      logging.info('removing cache file %s\n', outfile)
      os.remove(outfile)
    if not os.path.exists(outfile):
      logging.info('do process log file %s\n', logfile)
      process_logfile(name, outfile)
  if os.path.exists(outfile):
    logging.info('existing cache file %s\n', outfile)
#    if not os.path.exists(gzfile):
#      logging.info('compressing cache file %s\n', outfile)
#      with open(outfile, 'rb') as f_in:
#        with gzip.open(gzfile, mode='wb', compresslevel=9) as f_out:
#          shutil.copyfileobj(f_in, f_out)
#    logging.info('serving gzip file %s\n', gzfile)
    return web.FileResponse(outfile)

  raise web.HTTPFound(location='/klipper_logs')

async def handle_log_static(request: web.Request) -> web.StreamResponse:
  name = request.match_info.get("name", "invalid")
  file = f'cache/{name}.log'
  logging.info('serving static log file %s\n', file)
  if os.path.exists(file):
    response = web.FileResponse(file, chunk_size=256 * 1024)
    response.headers['Content-Type'] = 'text/plain'
    return response

  raise web.HTTPFound(location='/klipper_logs')

async def read_field(field, filename):
  d = hashlib.md5()
  size = 0
  with open(filename, 'wb') as f:
    try:
      while True:
        chunk = await field.read_chunk()  # 8192 bytes by default.
        if not chunk:
          break
        size += len(chunk)
        f.write(chunk)
        d.update(chunk)
    except:
      logging.warn(f'exception while processing {field} for {filename}')

  digest = d.hexdigest()
  print('received file', digest)
  return (size, digest)

_100MB = 1024 * 1024 * 100

async def upload_log(request: web.Request) -> web.StreamResponse:
  logging.info('serrving upload file\n')
  reader = await request.multipart()

  digest = ''

  tempname = ''
  moonraker = ''
  dmesg = ''
  debug = ''
  crownest = ''
  telegram = ''
  tarname = ''

  try:
    while True:
      field = await reader.next()
      if field is None:
        break

      fields = []

      if field.name == 'tarfile':
        print('received compressed logs')
        tarname = os.path.join('cache/', str(random.getrandbits(128)))
        size, digest = await read_field(field, tarname)
        if size < 100:
          os.remove(tarname)
          tarname = ''

      if field.name == 'logfile':
        print('received klippy log')
        tempname = os.path.join('cache/', str(random.getrandbits(128)))
        size, digest = await read_field(field, tempname)
        if size < 100:
          os.remove(tempname)
          tempname = ''

      if field.name == 'moonraker':
        print('received moonraker log')
        moonraker = os.path.join('cache/', str(random.getrandbits(128)))
        size, _ = await read_field(field, moonraker)
        if size < 100:
          os.remove(moonraker)
          moonraker = ''

      if field.name == 'dmesg':
        print('received dmesg log')
        dmesg = os.path.join('cache/', str(random.getrandbits(128)))
        size, _ = await read_field(field, dmesg)
        if size < 100:
          os.remove(dmesg)
          dmesg = ''

      if field.name == 'debug':
        print('received debug log')
        debug = os.path.join('cache/', str(random.getrandbits(128)))
        size, _ = await read_field(field, debug)
        if size < 100:
          os.remove(debug)
          debug = ''
  except:
    logging.warn('Exception reading fields')

  if tarname:
    tar = tarfile.open(tarname, "r:xz")
    print('content of tarfile:')
    temp_dest = os.path.join('cache/', digest)
    for member in tar.getmembers():
      print(member.name, member.size)
      if member.size > _100MB or member.size < 100:
        print('File too big')
        raise web.HTTPFound(location=f'/klipper_logs')
        break

      if not member.isfile():
        print('Not a file')
        raise web.HTTPFound(location=f'/klipper_logs')
        break

      if member.name == 'klippy.log':
        print('found klippy log')
        tempname = os.path.join(temp_dest, member.name)
        tar.extract(member, path=temp_dest)
        with open(tempname) as f,  mmap(f.fileno(), 0, access=ACCESS_READ) as file:
          digest = hashlib.md5(file).hexdigest()

      if member.name == 'moonraker.log':
        print('found moonraker log')
        moonraker = os.path.join(temp_dest, member.name)
        tar.extract(member, path=temp_dest)

      if member.name == 'dmesg.txt':
        print('found dmesg log')
        dmesg = os.path.join(temp_dest, member.name)
        tar.extract(member, path=temp_dest)

      if member.name == 'debug.txt':
        print('found debug log')
        debug = os.path.join(temp_dest, member.name)
        tar.extract(member, path=temp_dest)

      if member.name == 'crownest.log':
        print('found crownest log')
        crownest = os.path.join(temp_dest, member.name)
        tar.extract(member, path=temp_dest)

      if member.name == 'telegram.log':
        print('found telegram log')
        telegram = os.path.join(temp_dest, member.name)
        tar.extract(member, path=temp_dest)


    os.remove(tarname)
    tarname = temp_dest

  if tempname == '':
    print('tempname is empty')
    raise web.HTTPFound(location=f'/klipper_logs')
    return

  filename = f'cache/{digest}.log'
  moonraker_name = f'cache/{digest}_moonraker.log'
  dmesg_name = f'cache/{digest}_dmesg.log'
  debug_name = f'cache/{digest}_debug.log'
  crownest_name = f'cache/{digest}_crownest.log'
  telegram_name = f'cache/{digest}_telegram.log'
  html_name = f'cache/{digest}.html'

  logging.info('file: %s md5: %s\n', filename, digest)

  if not os.path.exists(filename):
    os.rename(tempname, filename)
  else: 
    os.remove(tempname)
    if moonraker:
      if not os.path.exists(moonraker_name):
        os.rename(moonraker, moonraker_name)
        os.remove(html_name)
      else:
        os.remove(moonraker)
    if dmesg:
      if not os.path.exists(dmesg_name):
        os.rename(dmesg, dmesg_name)
        os.remove(html_name)
      else:
        os.remove(dmesg)
    if debug:
      if not os.path.exists(debug_name):
        os.rename(debug, debug_name)
        os.remove(html_name)
      else:
        os.remove(debug)
    if crownest:
      if not os.path.exists(crownest_name):
        os.rename(crownest, crownest_name)
        os.remove(html_name)
      else:
        os.remove(crownest)
    if telegram:
      if not os.path.exists(telegram_name):
        os.rename(telegram, telegram_name)
        os.remove(html_name)
      else:
        os.remove(telegram)
    if tarname:
      os.rmdir(tarname)
    print('serving existing', digest)
    raise web.HTTPFound(location=f'/klipper_logs/{digest}')
    return

  if moonraker != '':
    os.rename(moonraker, moonraker_name)

  if dmesg != '':
    os.rename(dmesg, dmesg_name)

  if debug != '':
    os.rename(debug, debug_name)

  if crownest != '':
    os.rename(crownest, crownest_name)

  if telegram != '':
    os.rename(telegram, telegram_name)

  if tarname:
    os.rmdir(tarname)

  print('serving', digest)
  raise web.HTTPFound(location=f'/klipper_logs/{digest}')

def run(port=8998):
  logging.basicConfig(level=logging.INFO)

  app = web.Application()
  app.add_routes(
    [
      web.get("/", handle_index),
      web.get("//", handle_index),
      web.get("/list", handle_list),
      web.get("//list", handle_list),
      web.get("/upload", handle_upload),
      web.get("//upload", handle_upload),
      web.get("/getlogs", handle_getlogs),
      web.get("//getlogs", handle_getlogs),
      web.get("/getlogdev", handle_getlogdev),
      web.get("//getlogdev", handle_getlogdev),
      web.get("/{name}.log", handle_log_static),
      web.get("//{name}.log", handle_log_static),
      web.get("/index_{lang}.json", handle_lang),
      web.get("//index_{lang}.json", handle_lang),
      web.get("/{name}", handle_log),
      web.get("//{name}", handle_log),
      web.post("/", upload_log),
      web.post("//", upload_log),
      web.post("/upload", upload_log),
      web.post("//upload", upload_log),
    ]
  )

  try:
    web.run_app(app, port=port)
  except KeyboardInterrupt:
    pass
  logging.info('Stopping http server...\n')

if __name__ == '__main__':
  from sys import argv

  if len(argv) == 2:
    run(port=int(argv[1]))
  else:
    run()
