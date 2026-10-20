from atsfill.adapters.ashby import Ashby
from atsfill.adapters.greenhouse import Greenhouse
from atsfill.adapters.hireology import Hireology
from atsfill.adapters.jazzhr import JazzHR
from atsfill.adapters.lever import Lever
from atsfill.adapters.manatal import Manatal
from atsfill.adapters.oracle import Oracle
from atsfill.adapters.paycor import Paycor
from atsfill.adapters.paylocity import Paylocity
from atsfill.adapters.smartrecruiters import SmartRecruiters
from atsfill.adapters.teamtailor import Teamtailor
from atsfill.adapters.workable import Workable
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
    Paylocity(),
    Workable(),
    Hireology(),
    Paycor(),
    JazzHR(),
    Lever(),
]
