import json, time
from pathlib import Path
from server import model, evolve, SYSTEM

CASES = [
 ('hypoglycemia_trauma','使用长效胰岛素后未进食的成年人，低血糖伴事故擦伤。', [('检查血糖与意识',0),('患者清醒可吞咽，给予口服糖',1),('只处理擦伤，观察',15),('再次检查血糖',60)], '早期改善不等于长效胰岛素作用终止，注意复发；不能把擦伤治疗解释为纠正低血糖。'),
 ('renal_medication','肾功能不全、心衰、慢性疼痛的成年人。', [('核实既往用药并检查肾功能',0),('给予止痛药，但未指定品种剂量',1),('等待，未确认给药',20),('重新检查并比较',30)], '不明确给药必须澄清，不得在未执行时出现药效。'),
 ('asthma_allergy','有哮喘和药物过敏史的患者，喘息且发热。', [('听诊并测血氧',0),('给予吸入支气管扩张剂，常规成人方案',2),('等待并复查呼吸与心率',5),('询问过敏史并评估感染',10)], '扩张气道不等于感染治愈，注意心率等副作用与信息边界。'),
 ('bleeding_anticoagulant','长期抗凝患者摔倒，头痛，体表仅轻微损伤。', [('问诊并检查神经状态',0),('只包扎体表伤口',5),('等待20分钟',20),('安排头部影像',5)], '外伤表象不能代替内部状态；包扎不应自动消除颅内问题。'),
 ('delayed_interaction','感染伴糖尿病患者，治疗前有发热和轻度脱水。', [('测体温、血糖、血压',0),('按病例条件选择抗感染药并说明剂量依据',1),('一分钟后复查',1),('观察六十分钟再复查',60)], '不能一分钟内无依据完全治愈，时间顺序和多病症保留。'),
 ('novel_injury','原有发热、脱水的成年人，意识清楚。', [('询问感受',0),('意外被玻璃划伤左前臂',1),('持续加压止血并观察',5),('复查伤口与原有症状',10)], '新增损伤与原有疾病同时存在，止血不能治愈发热。')
]

if __name__=='__main__':
    out=Path(__file__).parent/'results';out.mkdir(exist_ok=True)
    dataset=[{'id':cid,'initial':initial,'turns':[{'action':a,'minutes':m} for a,m in turns],'rubric':rubric} for cid,initial,turns,rubric in CASES]
    (out/'dataset.json').write_text(json.dumps(dataset,ensure_ascii=False,indent=2),encoding='utf-8')
    reports=[]
    for cid,initial,turns,rubric in CASES:
        record={'id':cid,'rubric':rubric,'turns':[]}
        try:
            state,usage=model(SYSTEM,{'initialize':initial,'targetMinute':0})
            for action,minutes in turns:
                before=state
                state,usage,clarification=evolve(state,action,minutes)
                record['turns'].append({'action':action,'requestedMinutes':minutes,'committed':not bool(clarification),'actualElapsedMinutes':state.get('minute',0)-before.get('minute',0),'before':before,'after':state,'clarification':clarification,'usage':usage})
                print(cid,len(record['turns']),'ok',flush=True)
            review,_=model('你评审的是身体模拟器，不是玩家医疗水平。错误治疗和等待是故意设计的实验输入，不能仅因玩家治疗延迟、未做检查或错误治疗判模拟器失败；应检查模型是否合理模拟这些行为的后果。committed=false表示澄清而未执行，不推进时间是正确行为。检查已提交动作的绝对时序、病史一致性、药效延迟、多病症保留、无依据精确值和信息泄漏。每个问题要指出具体轮次、状态字段及违反的因果约束，区分确定错误与医学不确定性。JSON {"findings":[],"passed":boolean,"limitations":"同模型评审不能替代医学核验"}。',record)
            record['review']=review
        except Exception as e: record['error']=str(e)
        reports.append(record)
        (out/'report.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf-8')
        print(cid,'saved',flush=True)
