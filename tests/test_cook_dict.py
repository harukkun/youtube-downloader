import io
import json
from unittest.mock import patch
import app as m
from cook_dict import validate
from tests.test_upload_process import UploadProcessTest

RECIPE = {'title':'마늘 볶음밥','author':'','baseServings':None,'category':'one_bowl','difficulty':None,
          'ingredients':[{'name':'다진마늘','amount':'1/2','unit':'큰술','note':None,'isMain':True}],
          'steps':['마늘을 볶는다.']}

class CookDictTest(UploadProcessTest):
    def test_extract(self):
        with patch.object(m,'get_recipe_settings',return_value={'backend':'codex','model':'test'}), patch.object(m,'llm_structured',return_value=RECIPE):
            r=self.client.post('/api/upload-process/cook-dict',json={'description':'마늘을 볶는다.'})
            self.assertEqual(r.status_code,200)
            self.assertIsNone(r.json['recipe']['baseServings'])
            self.assertEqual(r.json['recipe']['ingredients'][0]['amount'],'1/2')

    def test_submit_requires_photo_and_valid_structure(self):
        payload=self.payload();payload['cook_dict']={**RECIPE,'version':1,'sourceChannel':'채널'}
        with patch.object(m,'apps_script_post',return_value=self.result) as post:
            r=self.client.post('/api/upload-process/submit',data={'payload':json.dumps(payload)})
            self.assertEqual(r.status_code,400);post.assert_not_called()
            r=self.client.post('/api/upload-process/submit',data={'payload':json.dumps(payload),'cook_photo':(io.BytesIO(b'\xff\xd8\xffphoto'),'photo.jpg')})
            r.request.close()
            self.assertEqual(r.status_code,200)
            sent=post.call_args.args[1]
            self.assertEqual(sent['cookDict']['description'],'description')
            self.assertEqual(sent['cookPhoto']['mime'],'image/jpeg')
            self.assertEqual(sent['cookDict']['author'],'')

    def test_repeat_rows_preserved_and_malformed_rejected(self):
        self.assertEqual(len(validate({**RECIPE,'ingredients':RECIPE['ingredients']*2})['ingredients']),2)
        for patch_value in [{'baseServings':True},{'ingredients':[]},{'steps':[]},{'category':'guess'}]:
            with self.assertRaises(ValueError):validate({**RECIPE,**patch_value})
