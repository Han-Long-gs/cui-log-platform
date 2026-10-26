import { type Level } from "../api.ts"

type BadgeProps = {
    level: Level
}

const Badge = (props: BadgeProps) => {
  return (
    <span className={`badge badge--${props.level.toLowerCase()} text-label`}>{props.level}</span>
  )
}

export default Badge