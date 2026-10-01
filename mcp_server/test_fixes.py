"""Checks for the review fixes in the MCP server, over the real MCP protocol against the FM-1:
 - blocking tools run in threads: fm1_status answers while fm1_meter records
 - fm1_stop stops a background sequence that is sitting in a long rest, within ~0.1 s
 - a second background sequence replaces the first (no interleaving)
 - fm1_set_params rejects bad names without applying any of the batch"""
import asyncio, json, pathlib, sys, time
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

HERE = pathlib.Path(__file__).parent


def body(res):
    if res.structured_content is not None:
        return res.structured_content
    txt = [c.text for c in res.content]
    try:
        return json.loads(txt[0])
    except Exception:
        return txt


async def main():
    params = StdioServerParameters(command=sys.executable, args=[str(HERE / 'fm1_mcp.py')])
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        call = s.call_tool
        await call('fm1_init_voice', {'preset': 'sine'})

        # 1) concurrency: status during a 3 s meter
        t0 = time.perf_counter()
        meter = asyncio.create_task(call('fm1_meter', {'seconds': 3}))
        await asyncio.sleep(0.3)
        await call('fm1_status', {})
        t_status = time.perf_counter() - t0
        await meter
        t_meter = time.perf_counter() - t0
        print(f'1) fm1_status returned at {t_status:.2f}s while fm1_meter(3) ran until {t_meter:.2f}s ->',
              'OK' if t_status < 2.5 else 'BLOCKED')

        # 2) stop during a long rest: note, 4 s rest, note
        seq = [{'note': 'A4', 'start': 0, 'dur': 200}, {'note': 'A5', 'start': 4000, 'dur': 200}]
        await call('fm1_play_sequence', {'events': seq, 'background': True})
        await asyncio.sleep(0.6)
        t0 = time.perf_counter(); await call('fm1_stop', {}); t_stop = time.perf_counter() - t0
        m = body(await call('fm1_meter', {'seconds': 4.5}))
        loud = [p for p in m['per_half_second'] if p['rms'] > -80]
        print(f'2) fm1_stop took {t_stop * 1000:.0f} ms; notes heard in the next 4.5 s: {len(loud)} ->',
              'OK' if not loud else f'LEAKED {loud}')

        # 3) replace: bg A (A4 every 400 ms) then immediately bg B (C3 every 400 ms); only B's pitch should sound
        a = [{'note': 'A5', 'start': i * 400, 'dur': 150} for i in range(10)]
        b = [{'note': 'A3', 'start': i * 400, 'dur': 150} for i in range(10)]
        await call('fm1_play_sequence', {'events': a, 'background': True})
        await asyncio.sleep(0.5)
        await call('fm1_play_sequence', {'events': b, 'background': True})
        rec = body(await call('fm1_record', {'seconds': 3.0, 'name': 'replace_test'}))
        print(f"3) after replacing, dominant pitch {rec.get('dominant_hz')} Hz (A3 = 220, A5 = 880) ->",
              'OK' if rec.get('dominant_hz') and abs(rec['dominant_hz'] - 220) < 15 else 'INTERLEAVED?')
        await call('fm1_stop', {})

        # 4) bad parameter batch is rejected as a whole
        before = body(await call('fm1_get_voice', {'full': True}))['params']['OP1.OL']
        res = await call('fm1_set_params', {'params': {'OP1.OL': 50, 'OP7.OL': 10}})
        after = body(await call('fm1_get_voice', {'full': True}))['params']['OP1.OL']
        print(f'4) set_params with OP7: error={res.is_error}, OP1.OL {before} -> {after} ->',
              'OK' if res.is_error and before == after else 'PARTIAL/ACCEPTED')
        await call('fm1_panic', {})

asyncio.run(main())
