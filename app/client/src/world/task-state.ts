import type {WorldTask} from './types'

type TaskState=Pick<WorldTask,'state'|'persistenceState'>

export function worldTaskFinished(task:TaskState){
  return task.state==='succeeded'||task.state==='cancelled'||task.state==='failed'&&task.persistenceState!=='recoverable'
}

export function worldTaskRecovering(task:TaskState|null|undefined){
  return !!task&&task.persistenceState==='recoverable'&&!worldTaskFinished(task)
}
