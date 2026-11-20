// API实体字段约定：来源{id,natural_key,data}、口径{id,version,data}、报告{id,status,snapshot,result,approved}、计划{next_at,enabled}、配对比较{id,status,generation,progress,result,published}。
export const ENTITY_KEYS=['sources','policies','runs','schedules','deliveries','comparisons'];
