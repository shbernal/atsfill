from atsfill.adapters.ashby import Ashby
from atsfill.adapters.greenhouse import Greenhouse
from atsfill.adapters.manatal import Manatal
from atsfill.adapters.oracle import Oracle
from atsfill.adapters.smartrecruiters import SmartRecruiters
from atsfill.adapters.teamtailor import Teamtailor
from atsfill.adapters.wttj import WelcomeToTheJungle
from atsfill.core.adapter import Adapter

ADAPTERS: list[Adapter] = [
    Ashby(),
    Teamtailor(),
    Greenhouse(),
    WelcomeToTheJungle(),
    SmartRecruiters(),
    Oracle(),
    Manatal(),
]
