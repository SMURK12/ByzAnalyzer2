import openai
import os
from dotenv import load_dotenv
import google.generativeai as genai


# Load environment variables
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY2")

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

class BusinessAI:
    def __init__(self, target_business_type, target_lat, target_lng, target_description, nearby_establishments, competitors, other_establishments,foot_traffic,demographics=None):
        self.target_type = target_business_type.lower()
        self.target_lat = target_lat
        self.target_lng = target_lng
        self.target_description = target_description
        self.nearby_establishments = nearby_establishments or []
        self.competitors = competitors or []
        self.demographics = demographics or {}
        self.other_establishments = other_establishments or []
        self.foot_traffic = foot_traffic or []

    def get_analysis(self):
        """
        Gemini-only generation using models/gemini-2.5-pro.
        Returns: (analysis_text: str, warnings: list[str])
        """
        warnings = []

        # Build the prompt from available fields
        try:
            bt = getattr(self, "target_type", getattr(self, "business_type", "business"))
            lat = getattr(self, "target_lat", getattr(self, "lat", ""))
            lng = getattr(self, "target_lng", getattr(self, "lng", ""))
            desc = getattr(self, "target_description", getattr(self, "description", "")) or ""
        except Exception:
            bt = getattr(self, "target_type", "business")
            lat, lng, desc = "", "", ""

        prompt_parts = [
            f"You are a practical business analyst. Provide a concise, actionable analysis.",
            f"You should focus more on competitor's menus/services etc. and their distances like realistically can their businesses affect my business.",
            
            f"Business type: {bt}",
            f"Location (lat,lng): {lat},{lng}",
            f"Description: {desc}",
        ]

        # competitors
        comps = getattr(self, "competitors", []) or []
        if comps:
            prompt_parts.append(f"Competitors ({len(comps)}):")
            for i, c in enumerate(comps[:12], 1):
                name = c.get("name", "Unknown")
                notes = c.get("notes", "") or c.get("vicinity", "") or ""
                prompt_parts.append(f"{i}. {name} — {notes}")

        # other establishments summary
        other = getattr(self, "other_establishments", []) or []
        if other:
            prompt_parts.append(f"Other nearby establishments: {len(other)} total (summarize types).")

        # demographics
        demo = getattr(self, "demographics", {}) or {}
        if demo:
            prompt_parts.append(f"Demographics summary: {demo}")
        prompt_parts.append(str(self.foot_traffic))
        prompt_parts.append(
            f"Please provide:\n"
            "1) A 2-3 sentence summary of opportunity.\n"
            "2) 3 numbered actionable recommendations.\n"
            "3) 3 numbered risks with one-line mitigations each.\n"
            "Be concise and practical."
        )

        prompt = "\n\n".join(prompt_parts)

        # Use the explicit model you requested
        model_id = "models/gemini-2.5-pro"

        # Try to generate using the google.generativeai GenerativeModel pattern
        try:
            # genai has been configured at module-level: genai.configure(api_key=...)
            # Use the GenerativeModel interface you validated earlier
            try:
                model = genai.GenerativeModel(model_name=model_id)
            except Exception as e:
                # If construction fails, surface the error
                warnings.append(f"Could not construct GenerativeModel({model_id}): {e}")
                model = None

            if model is not None:
                try:
                    resp = model.generate_content(prompt)
                except Exception as e:
                    warnings.append(f"generate_content call failed for {model_id}: {e}")
                    resp = None

                # Robust extraction of text from different response shapes
                if resp is not None:
                    extracted = None
                    try:
                        # Many client builds return resp.result or resp.candidates
                        # Attempt a few common access patterns:
                        # 1) resp.result.candidates -> each candidate has .content.parts[*].text
                        # 2) resp.candidates (older) -> candidate["content"]["parts"]
                        # 3) fallback to str(resp)
                        # Pattern 1:
                        if hasattr(resp, "result") and getattr(resp.result, "candidates", None):
                            pieces = []
                            for c in resp.result.candidates:
                                # c.content.parts may be present
                                content = getattr(c, "content", None)
                                if content and getattr(content, "parts", None):
                                    for p in content.parts:
                                        txt = getattr(p, "text", None)
                                        if txt:
                                            pieces.append(txt)
                                else:
                                    # sometimes content has .text or candidate has .text
                                    t = getattr(c, "text", None) or getattr(c, "content", None)
                                    if isinstance(t, str):
                                        pieces.append(t)
                            if pieces:
                                extracted = "".join(pieces)
                        # Pattern 2:
                        if not extracted and hasattr(resp, "candidates"):
                            pieces = []
                            for c in getattr(resp, "candidates", []):
                                cont = c.get("content") if isinstance(c, dict) else None
                                if cont and cont.get("parts"):
                                    for p in cont.get("parts", []):
                                        if isinstance(p, dict) and p.get("text"):
                                            pieces.append(p.get("text"))
                                elif isinstance(c, dict) and c.get("text"):
                                    pieces.append(c.get("text"))
                            if pieces:
                                extracted = "".join(pieces)
                        # Pattern 3: dict-like response with 'candidates'
                        if not extracted and isinstance(resp, dict) and resp.get("candidates"):
                            pieces = []
                            for c in resp.get("candidates", []):
                                cont = c.get("content", {})
                                for p in cont.get("parts", []):
                                    if isinstance(p, dict) and p.get("text"):
                                        pieces.append(p.get("text"))
                            if pieces:
                                extracted = "".join(pieces)
                        # Final fallback
                        if not extracted:
                            extracted = None
                            try:
                                extracted = getattr(resp, "text", None)
                            except Exception:
                                extracted = None
                        if not extracted:
                            extracted = str(resp)
                    except Exception as e:
                        warnings.append(f"Error extracting text from Gemini response: {e}")
                        extracted = str(resp)

                    if extracted:
                        return extracted.strip(), warnings
                    else:
                        warnings.append("Gemini returned no text (empty extraction).")
            else:
                warnings.append("Gemini model object was not created.")
        except Exception as e:
            warnings.append(f"Unexpected error using Gemini model {model_id}: {e}")

        # If we reached here, Gemini generation did not succeed — return a helpful mock and warnings
        mock = self._generate_mock_analysis() if hasattr(self, "_generate_mock_analysis") else (
            f"⚠️ Mock analysis for {bt} at {lat},{lng}\n\n"
            "Unable to generate live Gemini analysis. See warnings for details."
        )
        if warnings:
            mock = mock + "\n\nWarnings:\n- " + "\n- ".join(warnings)
        return mock, warnings


    def _generate_mock_analysis(self):
        """Generate a mock analysis for testing purposes"""
        analysis = f"""
**Business Location Analysis for {self.target_type.title()}**

**Location:** {self.target_lat}, {self.target_lng}

**Market Opportunity:**
Based on the analysis of the target location, this area shows {'strong' if self.demographics.get('total', 0) > 5000 else 'moderate'} potential for a {self.target_type} business. The population density and demographic composition suggest {'favorable' if self.demographics.get('total', 0) > 3000 else 'mixed'} market conditions.

**Competition Analysis:**
{'High competition detected' if len(self.competitors) > 5 else 'Moderate competition' if len(self.competitors) > 2 else 'Low competition'} in the immediate area with {len(self.competitors)} direct competitors identified. This suggests {'saturated' if len(self.competitors) > 5 else 'competitive' if len(self.competitors) > 2 else 'opportunity'} market conditions.


Total population: {self.demographics.get('total', 0):,} people
Gender distribution: {self.demographics.get('male', 0):,} male, {self.demographics.get('female', 0):,} female
Age groups: Children ({self.demographics.get('children', 0):,}), Teens ({self.demographics.get('teens', 0):,}), Young Adults ({self.demographics.get('young_adults', 0):,}), Adults ({self.demographics.get('adults', 0):,}), Seniors ({self.demographics.get('seniors', 0):,})
**Foot Traffic Insights:**
**Recommendations:**
1. Focus on {'family-oriented' if self.demographics.get('children', 0) > self.demographics.get('adults', 0) else 'adult-focused'} offerings
2. Consider {'premium' if self.demographics.get('adults', 0) > 2000 else 'budget-friendly'} pricing strategy
3. {'High' if len(self.competitors) > 5 else 'Moderate'} differentiation needed to stand out

**Risk Assessment:**
Primary risks include market saturation and demographic shifts. Mitigation strategies should focus on unique value propositions and flexible business models.

*Note: This is a mock analysis generated for testing purposes. AI integration is being configured.*
        """
        return analysis.strip()

    def identify_competitors_with_ai(self, all_establishments):
        """
        Use AI to intelligently identify which establishments are actual competitors.
        Returns a list of competitor establishments with reasoning.
        
        Args:
            all_establishments: List of all nearby establishments
            
        Returns:
            dict: {
                'competitors': List of competitor establishments (subset of input),
                'competitor_indices': List of indices from original all_establishments list,
                'reasoning': str explaining the competitor selection
            }
        """
        if not all_establishments:
            return {
                'competitors': [],
                'competitor_indices': [],
                'reasoning': 'No establishments provided for analysis.'
            }
        
        # Prepare minimal data for AI analysis (save tokens)
        establishments_summary = []
        for idx, est in enumerate(all_establishments[:50]):  # Limit to 50 to save tokens
            establishments_summary.append({
                'idx': idx,
                'name': est.get('name', 'Unknown'),
                'types': est.get('all_types', [])[:3],  # Only first 3 types
                'distance_m': self._calculate_distance(
                    self.target_lat, 
                    self.target_lng, 
                    est.get('lat'), 
                    est.get('lng')
                ) if est.get('lat') and est.get('lng') else None,
                'rating': est.get('rating'),
                'vicinity': est.get('vicinity', '')[:50]  # Truncate to 50 chars
            })
        
        # Build concise prompt
        prompt = f"""Analyze which establishments are TRUE COMPETITORS for this business:

    TARGET BUSINESS:
    - Type: {self.target_type}
    - Description: {self.target_description}
    - Location: {self.target_lat}, {self.target_lng}

    NEARBY ESTABLISHMENTS ({len(establishments_summary)} total):
    """
        
        for est in establishments_summary:
            prompt += f"\n[{est['idx']}] {est['name']} | Types: {', '.join(est['types'][:2])} | Distance: {est['distance_m']}m"
        
        prompt += """

    TASK: Return ONLY the indices (numbers in brackets) of establishments that are DIRECT COMPETITORS.
    - Direct competitors: Same business category, serve similar customer needs, compete for same customers
    - NOT competitors: Complementary businesses, different market segments, too far away (>1km)

    OUTPUT FORMAT (JSON):
    {
    "competitor_indices": [1, 5, 12],
    "reasoning": "Brief explanation of why these are competitors"
    }

    Be selective - only include true direct competitors."""

        try:
            model = genai.GenerativeModel(model_name="models/gemini-2.0-flash-exp")
            
            # Request JSON output
            response = model.generate_content(
                prompt,
                generation_config={
                    "temperature": 0.3,  # Lower temperature for more consistent output
                    "response_mime_type": "application/json"
                }
            )
            
            # Extract text from response
            result_text = None
            if hasattr(response, 'text'):
                result_text = response.text
            elif hasattr(response, 'candidates') and response.candidates:
                result_text = response.candidates[0].content.parts[0].text
            
            if not result_text:
                raise ValueError("No text returned from AI")
            
            # Parse JSON response
            import json
            result = json.loads(result_text)
            
            competitor_indices = result.get('competitor_indices', [])
            reasoning = result.get('reasoning', 'No reasoning provided')
            
            # Build competitor list from indices
            competitors = []
            for idx in competitor_indices:
                if 0 <= idx < len(all_establishments):
                    competitors.append(all_establishments[idx])
            
            return {
                'competitors': competitors,
                'competitor_indices': competitor_indices,
                'reasoning': reasoning,
                'total_analyzed': len(establishments_summary),
                'total_found': len(competitors)
            }
            
        except Exception as e:
            # Fallback to simple type-matching
            print(f"AI competitor identification failed: {e}")
            fallback_competitors = []
            fallback_indices = []
            
            for idx, est in enumerate(all_establishments):
                if est and self.target_type in est.get('all_types', []):
                    fallback_competitors.append(est)
                    fallback_indices.append(idx)
            
            return {
                'competitors': fallback_competitors,
                'competitor_indices': fallback_indices,
                'reasoning': f'Fallback: Type-matching found {len(fallback_competitors)} competitors (AI failed: {str(e)})',
                'total_analyzed': len(all_establishments),
                'total_found': len(fallback_competitors)
            }
        
    def _calculate_distance(self, lat1, lng1, lat2, lng2):
        """Calculate distance between two coordinates in meters using Haversine formula."""
        if None in (lat1, lng1, lat2, lng2):
            return None
        
        from math import radians, sin, cos, sqrt, atan2
        
        R = 6371000  # Earth's radius in meters
        
        lat1_rad = radians(lat1)
        lat2_rad = radians(lat2)
        delta_lat = radians(lat2 - lat1)
        delta_lng = radians(lng2 - lng1)
        
        a = sin(delta_lat/2)**2 + cos(lat1_rad) * cos(lat2_rad) * sin(delta_lng/2)**2
        c = 2 * atan2(sqrt(a), sqrt(1-a))
        
        return int(R * c)