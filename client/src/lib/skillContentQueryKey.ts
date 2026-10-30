/** Saved v1 Master Skill holders own their defaults; legacy cache keys stay unchanged. */
export const skillContentQueryKey = (skillName: string, nodeId?: string, version = 0) =>
  version === 1 && nodeId ? ['skillContent', skillName, 'holder', nodeId] : ['skillContent', skillName];
