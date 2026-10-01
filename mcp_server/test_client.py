"""Smoke test: start fm1_mcp.py over stdio as an MCP client would, list tools, call them against the real FM-1."""
import asyncio, json, pathlib, sys
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

HERE = pathlib.Path(__file__).parent


async def main():
    params = StdioServerParameters(command=sys.executable, args=[str(HERE / 'fm1_mcp.py')])
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        tools = (await s.list_tools()).tools
        print(f'{len(tools)} tools:', ', '.join(t.name for t in tools))

        async def call(tool, **args):
            res = await s.call_tool(tool, args)
            body = res.structured_content if res.structured_content is not None else [c.text for c in res.content]
            txt = json.dumps(body)
            print(f'\n> {tool}({", ".join(f"{k}={v!r}" for k, v in args.items())})' + (' ERROR' if res.is_error else ''))
            print('  ' + (txt[:600] + ('…' if len(txt) > 600 else '')))
            return body

        await call('fm1_status')
        await call('fm1_search_voices', category='kick', limit=3)
        await call('fm1_init_voice', preset='sine')
        await call('fm1_measure', note='A4', velocity=100, duration_ms=400)
        await call('fm1_set_params', params={'OP1.FC': 2})
        await call('fm1_measure', note='A4', velocity=100, duration_ms=400)           # expect ~880 Hz
        await call('fm1_output_trim', level=20)
        await call('fm1_measure', note='A4', velocity=100, duration_ms=400)           # expect ~-14 dB
        await call('fm1_output_trim', level=-1)
        await call('fm1_load_voice', voice='E.PIANO 1', audition=False)
        await call('fm1_play_sequence', bpm=120, events=[
            {'note': 'C2', 'start': 0, 'voice': 'SLPBK  BD'}, {'note': 'E4', 'start': 2, 'voice': 'E.PIANO 1', 'dur': 2},
            {'note': 'C4', 'start': 4, 'voice': 'Xylosnare'}, {'note': 'G4', 'start': 6, 'voice': 'E.PIANO 1', 'dur': 2}])
        await call('fm1_record', seconds=2.5, name='mcp_smoke', bpm=140, play=[
            {'note': 'C2', 'start': i * 2, 'voice': 'SLPBK  BD' if i % 2 == 0 else 'Xylosnare'} for i in range(8)])
        await call('fm1_panic')

asyncio.run(main())
