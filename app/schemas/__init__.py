"""请求体模型（Pydantic）。"""
from pydantic import BaseModel


class ResponseInitiate(BaseModel):
    """发起处置协同：挂接预报运行，调度员身份。"""
    run_id: int
    dispatcher: str = ""
    title: str = ""


class ResponseReview(BaseModel):
    """预警值守审核：通过进入待执行，不通过退回发起。"""
    duty_officer: str = ""
    approve: bool = True
    note: str = ""


class ResponseExecute(BaseModel):
    """转移负责人执行：回写审核后的调度方案。"""
    evac_lead: str = ""


class ResponseComplete(BaseModel):
    """完成闭环：转移安置确认、预警销号。"""
    actor: str = ""
