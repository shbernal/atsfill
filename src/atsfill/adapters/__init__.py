from atsfill.adapters.ashby import Ashby
from atsfill.adapters.teamtailor import Teamtailor
from atsfill.core.adapter import Adapter

ADAPTERS: list[Adapter] = [Ashby(), Teamtailor()]
