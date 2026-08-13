export type QuestionsSpaceView = "question_bank" | "paper_reorder";

export type PaperOrder = "source" | "easy_to_hard" | "manual";
export type UnassignedPolicy = "append" | "reject";
export type PaperExportFormat = "html" | "markdown";

export interface PaperSectionEditorValue {
  key: string;
  id: string;
  title: string;
  questionType: string;
  difficulty: string;
  knowledgePointIds: string;
  order: PaperOrder;
  questionIds: string;
}

export interface PaperReorderPayload {
  source_id: string;
  rules: {
    sections: Array<{
      id: string;
      title: string;
      filter: {
        question_type: string | null;
        difficulty: string | null;
        knowledge_point_ids: string[];
      };
      order: PaperOrder;
      question_ids: string[];
    }>;
    unassigned_policy: UnassignedPolicy;
  };
  include_answer_sheet: boolean;
}

export interface ReorderedQuestion {
  display_number: string;
  source_question_id: string;
  original_number: string;
  original_index: number;
  question_text: string;
  images: string[];
  question_type: string;
  difficulty: string;
  knowledge_point_ids: string[];
}

export interface ReorderedPaper {
  id: string;
  source_id: string;
  sections: Array<{
    id: string;
    title: string;
    questions: ReorderedQuestion[];
  }>;
  answer_sheet: Array<{
    display_number: string;
    source_question_id: string;
    original_number: string;
    answer: string;
  }> | null;
  audit: {
    input_count: number;
    output_count: number;
    unassigned_ids: string[];
    duplicate_ids: string[];
  };
  missing_image_refs: string[];
  warnings: string[];
}
