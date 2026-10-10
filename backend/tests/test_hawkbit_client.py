import asyncio

import httpx

from app.targets.hawkbit import HawkbitClient


def test_business_observation_paginates_beyond_500_devices_once():
    async def go():
        offsets = []
        def handler(request):
            offset = int(request.url.params["offset"])
            offsets.append(offset)
            page = [{"controllerId": f"cs-test-{i}", "updateStatus": "in_sync" if i < 550 else "pending"}
                    for i in range(offset, min(offset + 500, 600))]
            return httpx.Response(200, json={"content": page, "total": 600})
        client = HawkbitClient()
        await client.http.aclose()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client.http = http
            queued, stored = await client.observed_ids("cs-test-")
        assert len(stored) == 550 and len(queued) == 50
        assert offsets == [0, 500]
    asyncio.run(go())


def test_ignored_pagination_is_unknown_not_fake_loss():
    async def go():
        def handler(request):
            return httpx.Response(200, json={"content": [{"controllerId": "cs-test-0"}], "total": 600})
        client = HawkbitClient()
        await client.http.aclose()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client.http = http
            assert await client.observed_ids("cs-test-") == (None, None)
    asyncio.run(go())
