from __future__ import annotations

import copy

from sqlalchemy.orm import Session

from app.db.models import Campaign


def preference_style(campaign: Campaign) -> str:
    return "dice_assistant" if (campaign.config or {}).get("play_style") == "dice_assistant" else "campaign"


def combat_preference(campaign: Campaign, option: str) -> bool:
    style = preference_style(campaign)
    key = f"{style}_combat_{option}_enabled"
    default = style == "campaign"
    return bool((campaign.config or {}).get(key, default))


def set_combat_preference(db: Session, campaign: Campaign, option: str, enabled: bool) -> str:
    style = preference_style(campaign)
    key = f"{style}_combat_{option}_enabled"
    config = copy.deepcopy(campaign.config or {})
    config[key] = enabled
    campaign.config = config
    db.commit()
    return key
