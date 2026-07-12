export interface UserProfile {
  user_id: string;
  name: string;
  role: string;
  preferences?: string;
  interests?: unknown[];
  current_projects?: unknown[];
  communication_style?: Record<string, unknown>;
  life_experiences?: string;
  writing_patterns?: Record<string, unknown>;
}

export interface Account {
  account_id: string;
  name: string;
  industry?: string;
  website?: string;
  stage?: string;
  region?: string;
  notes?: string;
  research_summary?: string;
}

export interface Contact {
  contact_id: string;
  account_id: string;
  name: string;
  title?: string;
  department?: string;
  role_in_deal?: string;
  email?: string;
  phone?: string;
  notes?: string;
}

export interface Deal {
  deal_id: string;
  account_id: string;
  name: string;
  stage?: string;
  service_line?: string;
  expected_value?: number;
  close_date?: string;
  probability?: number;
  next_step?: string;
}

export interface Activity {
  activity_id: string;
  entity_type: string;
  entity_id: string;
  activity_type: string;
  direction?: string;
  content: string;
  sentiment?: string;
  created_at: string;
}

export interface Briefing {
  id: number;
  content: string;
  effective_date: string;
}

export interface Override {
  id: number;
  content: string;
  priority: number;
}

export interface EpisodicMessage {
  role: string;
  content: string;
  created_at: string;
}
