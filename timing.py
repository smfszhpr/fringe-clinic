import math

PROMPT = '''你是医学模拟游戏的独立行动时间估计器，不推演病情，不决定检查结果。根据行动、设备环境和已有证据估计游戏内分钟数。区分准备、实际操作、等待报告。治疗的操作耗时与药理起效不同；药效时间由身体模拟器处理，不能假设给药完成等于治愈。问诊也需要时间。明确等待则使用用户指定时长。复合动作按实际先后描述，避免同一时段重复计算。开放理解任意检查治疗，不限于固定列表。无法确定操作细节时needsClarification提问，不编造精确时间。时间只是模拟估计，列明假设与区间，不声称来自指南。JSON {"label":"行动名称","preparationMinutes":数字,"procedureMinutes":数字,"reportWaitMinutes":数字,"rangeMinutes":[下限,上限],"assumptions":["..."],"needsClarification":null}。医疗环境默认院内急诊、设备可用但需操作和报告，无真实排队数据。'''

def estimate(model, action, public_state):
    prompt = PROMPT + (
        '\n若玩家明确委托游戏内有资质的团队选择实现方式，且行动目标与范围清楚，'
        '按合理场景假设给出较宽的耗时区间，并在 assumptions 明示假设。'
        '避免追问已明确委托的细节；这只是耗时计划，不保证实际执行或成功。'
    )
    plan,usage=model(prompt,{'action':action,'knownState':public_state})
    if plan.get('needsClarification'): return plan,usage
    for key in ['preparationMinutes','procedureMinutes','reportWaitMinutes']:
        value=plan.get(key)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=10080:
            raise ValueError('耗时估计无效，请重新估计')
    plan['totalMinutes']=round(sum(plan[k] for k in ['preparationMinutes','procedureMinutes','reportWaitMinutes']),2)
    if plan['totalMinutes']<=0: raise ValueError('行动耗时必须大于零')
    plan['source']='AI估计，尚未接入经核对的耗时资料库'
    return plan,usage
